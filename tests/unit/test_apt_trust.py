"""Offline bounds and drift checks for native source/key snapshots."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from package_managers.apt_trust import trust_fingerprint
from package_managers.base import AdapterError


class APTTrustTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.keys = self.root / "keyrings"
        self.keys.mkdir()
        self.key = self.keys / "archive.gpg"
        self.key.write_bytes(b"Synthetic public key")
        self.source = self.root / "ubuntu.sources"
        self.source.write_text(f"# comment\n\nTypes: deb\nSigned-By: {self.key}\n")

    def snapshot(self, entries=None):
        return trust_fingerprint(
            entries or f"{self.source}:1\n",
            source_roots=(self.root,),
            key_roots=(self.keys,),
        )

    def test_content_and_reference_changes_invalidate_and_restoration_is_stable(self):
        before = self.snapshot()
        self.key.write_bytes(b"Different public key")
        self.assertNotEqual(self.snapshot(), before)
        self.key.write_bytes(b"Synthetic public key")
        self.assertEqual(self.snapshot(), before)
        other = self.keys / "other.gpg"
        other.write_bytes(self.key.read_bytes())
        self.source.write_text(f"Types: deb\nSigned-By: {other}\n")
        self.assertNotEqual(self.snapshot(), before)

    def test_legacy_manifest_explicit_key_and_duplicate_native_rows(self):
        self.source = self.root / "ubuntu.list"
        self.source.write_text(
            f"deb [signed-by={self.key}] https://archive.ubuntu.com/ubuntu noble main\n"
        )
        self.assertEqual(self.snapshot(), self.snapshot(f"{self.source}:1\n" * 5))

    def test_global_fingerprint_inline_and_missing_keys_fail_closed(self):
        for value in (
            "",
            "A" * 40,
            "-----BEGIN PGP PUBLIC KEY BLOCK-----",
            str(self.keys / "missing.gpg"),
        ):
            with self.subTest(value=value):
                self.source.write_text(f"Types: deb\nSigned-By: {value}\n")
                with self.assertRaises(AdapterError):
                    self.snapshot()

    def test_native_paths_cannot_escape_roots_or_select_unreviewed_files(self):
        secret = self.root / "private.gpg"
        secret.write_bytes(b"Unreviewed data")
        self.source.write_text(f"Types: deb\nSigned-By: {secret}\n")
        with self.assertRaises(AdapterError):
            self.snapshot()
        for entries in (
            "$(SOURCESENTRY)\n",
            "/outside/ubuntu.sources:1\n",
            f"{self.source}:1\n" * 513,
        ):
            with self.subTest(entries=entries[:40]):
                with self.assertRaises(AdapterError):
                    self.snapshot(entries)

    def test_oversized_files_and_symlinks_are_refused(self):
        self.key.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
        with self.assertRaises(AdapterError):
            self.snapshot()
        with mock.patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaises(AdapterError):
                self.snapshot()

    def test_concurrent_file_change_does_not_produce_a_snapshot(self):
        import package_managers.apt_trust as trust

        original = trust.os.fstat
        calls = 0

        def changed(descriptor):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.source.write_text("Changed while being read")
            return original(descriptor)

        with mock.patch.object(trust.os, "fstat", side_effect=changed):
            with self.assertRaises(AdapterError):
                self.snapshot()
