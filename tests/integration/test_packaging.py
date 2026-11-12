"""Bundle/release contracts without native installations or signing credentials."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import build_desktop, desktop_entry, release_manifest, sign_desktop


class PackagingTests(unittest.TestCase):
    def test_unattended_frozen_check_failure_returns_nonzero_without_stderr(self):
        with (
            mock.patch.object(
                desktop_entry, "self_check", side_effect=RuntimeError("synthetic error")
            ),
            mock.patch("sys.argv", ["DevSetup", "--smoke-test"]),
            mock.patch("sys.stderr", None),
        ):
            self.assertEqual(desktop_entry.main(), 1)

    def test_archive_and_metadata_hardlinks_are_refused_without_modifying_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dist" / "DevSetup").mkdir(parents=True)
            outside = root / "external-file"
            outside.write_bytes(b"must remain unchanged")
            host = build_desktop.detect_platform()
            suffix = ".zip" if sys.platform in {"darwin", "win32"} else ".tar.gz"
            artifact = (
                root
                / "dist"
                / f"DevSetup-{build_desktop.__version__}-{host.os.value}-{host.architecture.value}{suffix}"
            )
            for leaf in (artifact, artifact.with_name(artifact.name + ".json")):
                os.link(outside, leaf)
                with mock.patch.object(build_desktop, "ROOT", root):
                    with self.assertRaises(ValueError):
                        build_desktop.archive_bundle()
                self.assertEqual(outside.read_bytes(), b"must remain unchanged")
                leaf.unlink()

    def test_archive_publication_hashes_actual_bytes_and_replaces_regular_outputs(self):
        if sys.platform == "darwin":
            self.skipTest("macOS ditto packaging is a native CI gate")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root / "dist" / "DevSetup"
            bundle.mkdir(parents=True)
            (bundle / "synthetic-not-executed.txt").write_bytes(b"bundle data")
            with mock.patch.object(build_desktop, "ROOT", root):
                artifact = build_desktop.archive_bundle()
                first = artifact.read_bytes()
                artifact = build_desktop.archive_bundle()
            metadata = json.loads(
                artifact.with_name(artifact.name + ".json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                metadata["sha256"], hashlib.sha256(artifact.read_bytes()).hexdigest()
            )
            self.assertTrue(first)
            self.assertFalse(list((root / "dist").glob(".dev-setup-archive-*")))

    def test_archive_symlink_leaves_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "dist").mkdir()
            target = root / "private-file"
            target.write_bytes(b"preserve")
            link = root / "dist" / "archive.zip"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("Symlink creation is unavailable on this host")
            with mock.patch.object(build_desktop, "ROOT", root):
                with self.assertRaises(ValueError):
                    build_desktop.checked_leaf(link)
            self.assertEqual(target.read_bytes(), b"preserve")

    def test_source_self_check_uses_no_native_process_or_user_state(self):
        with (
            mock.patch(
                "subprocess.Popen", side_effect=AssertionError("native command")
            ),
            mock.patch("sqlite3.connect", side_effect=AssertionError("state write")),
        ):
            desktop_entry.self_check()

    def test_preview_entry_point_reports_embedded_version(self):
        result = subprocess.run(
            [sys.executable, "scripts/desktop_entry.py", "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
            shell=False,
        )
        self.assertEqual(result.stdout.strip(), desktop_entry.__version__)

    def test_cleanup_paths_reject_outside_repository_and_symlinks(self):
        with self.assertRaises(ValueError):
            build_desktop.checked_directory(build_desktop.ROOT.parent / "outside")
        with self.assertRaises(ValueError):
            build_desktop.checked_directory(build_desktop.ROOT)
        self.assertEqual(
            build_desktop.checked_directory(
                build_desktop.ROOT / "build" / "test-cache"
            ),
            build_desktop.ROOT / "build" / "test-cache",
        )

    def test_pyinstaller_uses_scoped_cache_and_no_shell(self):
        with mock.patch("scripts.build_desktop.subprocess.run") as run:
            build_desktop.run(["python", "-m", "PyInstaller"])
        self.assertFalse(run.call_args.kwargs["shell"])
        self.assertEqual(
            Path(run.call_args.kwargs["env"]["PYINSTALLER_CONFIG_DIR"]),
            build_desktop.ROOT / "build" / "pyinstaller-cache",
        )

    def test_preview_and_mismatched_versions_cannot_be_signed(self):
        for value in (release_manifest.__version__, "999.0.0", "0.1.0;echo bad"):
            with self.assertRaises(ValueError):
                release_manifest.release_version(value)

    def test_missing_native_signing_configuration_fails_closed(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            for platform in ("win32", "darwin"):
                with mock.patch("scripts.sign_desktop.sys.platform", platform):
                    with self.assertRaises(ValueError):
                        sign_desktop.check_credentials()

    def test_windows_signing_uses_thumbprint_not_password_arguments(self):
        environment = {
            "DEV_SETUP_WINDOWS_CERT_SHA1": "1" * 40,
            "DEV_SETUP_TIMESTAMP_URL": "https://timestamp.example.test/",
        }
        with (
            mock.patch.dict(os.environ, environment, clear=True),
            mock.patch("scripts.sign_desktop.sys.platform", "win32"),
            mock.patch(
                "scripts.sign_desktop.shutil.which", return_value="signtool.exe"
            ),
            mock.patch("scripts.sign_desktop.subprocess.run") as run,
        ):
            self.assertTrue(
                sign_desktop.sign_bundle(Path("bundle"), Path("bundle/DevSetup.exe"))
            )
        calls = [call.args[0] for call in run.call_args_list]
        self.assertEqual(
            calls[0],
            [
                "signtool.exe",
                "sign",
                "/sha1",
                "1" * 40,
                "/fd",
                "SHA256",
                "/tr",
                "https://timestamp.example.test/",
                "/td",
                "SHA256",
                str(Path("bundle/DevSetup.exe")),
            ],
        )
        self.assertEqual(calls[1][1:4], ["verify", "/pa", "/all"])
        self.assertTrue(
            all(call.kwargs["shell"] is False for call in run.call_args_list)
        )

    def test_signed_feed_requires_all_platforms_and_checks_actual_archive_hashes(self):
        try:
            import cryptography  # noqa: F401
        except ImportError:
            self.skipTest("Optional signature dependency is absent")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for platform in ("windows", "macos", "linux"):
                filename = f"DevSetup-1.0.0-{platform}-x86_64.zip"
                content = (platform + "-synthetic-not-executed").encode()
                (root / filename).write_bytes(content)
                (root / (filename + ".json")).write_text(
                    json.dumps(
                        {
                            "version": "1.0.0",
                            "os": platform,
                            "architecture": "x86_64",
                            "filename": filename,
                            "sha256": hashlib.sha256(content).hexdigest(),
                            "size": len(content),
                            "signed": platform != "linux",
                        }
                    ),
                    encoding="utf-8",
                )
            with (
                mock.patch("scripts.release_manifest.__version__", "1.0.0"),
                mock.patch.dict(
                    os.environ, {"DEV_SETUP_RELEASE_SIGNING_KEY": "01" * 32}
                ),
            ):
                manifest, signature = release_manifest.produce(
                    root, "1.0.0", 3, "https://releases.example.test/stable/"
                )
                self.assertEqual(len(signature), 64)
                self.assertEqual(len(json.loads(manifest)["artifacts"]), 3)
                (root / "DevSetup-1.0.0-linux-x86_64.zip").write_bytes(b"modified")
                with self.assertRaises(ValueError):
                    release_manifest.produce(
                        root, "1.0.0", 3, "https://releases.example.test/stable/"
                    )
