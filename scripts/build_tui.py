"""Build standalone terminal bundles; no publish, installation or native package query."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import __version__  # noqa: E402
from system.platform import detect_platform  # noqa: E402


def checked_directory(path: Path) -> Path:
    """Inspect every destination ancestor before PyInstaller can remove files."""
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT) or resolved == ROOT:
        raise ValueError("Build output/cache must remain inside this repository.")
    for candidate in (path, *path.parents):
        if candidate == ROOT:
            break
        if candidate.is_symlink() or (
            candidate.exists()
            and getattr(candidate.lstat(), "st_file_attributes", 0) & 0x400
        ):
            raise ValueError("Build output/cache cannot use symlinks or junctions.")
    return resolved


def run(arguments: list[str], *, timeout: int = 900) -> None:
    environment = os.environ.copy()
    environment["PYINSTALLER_CONFIG_DIR"] = str(
        checked_directory(ROOT / "build" / "pyinstaller-cache")
    )
    subprocess.run(
        arguments, cwd=ROOT, env=environment, check=True, timeout=timeout, shell=False
    )


def bundle_path() -> Path:
    return ROOT / "dist" / ("DevSetup.app" if sys.platform == "darwin" else "DevSetup")


def executable_path() -> Path:
    bundle = bundle_path()
    if sys.platform == "darwin":
        return bundle / "Contents" / "MacOS" / "DevSetup"
    return bundle / ("DevSetup.exe" if sys.platform == "win32" else "DevSetup")


def verify_standalone(executable: Path | None = None) -> None:
    """Exercise the embedded interpreter with no Python or tools available on PATH."""
    target = (executable or executable_path()).resolve(strict=True)
    if not target.is_file():
        raise ValueError("Standalone executable must be a regular file.")
    environment = os.environ.copy()
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        environment.pop(name, None)
    environment["PATH"] = ""
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    with tempfile.TemporaryDirectory(prefix="dev-setup-standalone-") as directory:
        # Keep qualification state outside the developer or runner home directory.
        environment["HOME"] = directory
        environment["USERPROFILE"] = directory
        environment["LOCALAPPDATA"] = str(Path(directory) / "state")
        environment["APPDATA"] = str(Path(directory) / "state")
        environment["XDG_CONFIG_HOME"] = str(Path(directory) / "config")
        environment["XDG_DATA_HOME"] = str(Path(directory) / "data")
        environment["XDG_STATE_HOME"] = str(Path(directory) / "state")
        for arguments in (
            ("--version",),
            ("catalog", "validate"),
            ("--self-check",),
            ("--smoke-test",),
        ):
            subprocess.run(
                [str(target), *arguments],
                cwd=directory,
                env=environment,
                check=True,
                timeout=60,
                shell=False,
            )


def checked_leaf(path: Path) -> Path:
    checked_directory(path.parent)
    if path.is_symlink() or not path.resolve().is_relative_to(ROOT):
        raise ValueError("Archive output cannot follow a symbolic link.")
    if path.exists():
        stat = path.lstat()
        if (
            not path.is_file()
            or getattr(stat, "st_file_attributes", 0) & 0x400
            or stat.st_nlink != 1
        ):
            raise ValueError("Archive output must be a regular file with one link.")
    return path


def archive_bundle(*, signed: bool = False) -> Path:
    host = detect_platform()
    if host.os.value not in {
        "windows",
        "macos",
        "linux",
    } or host.architecture.value not in {"x86_64", "arm64"}:
        raise ValueError("No release artifact target exists for this OS/architecture.")
    stem = f"DevSetup-{__version__}-{host.os.value}-{host.architecture.value}"
    bundle = checked_directory(bundle_path())
    destination = checked_directory(ROOT / "dist")
    suffix = ".zip" if sys.platform in {"darwin", "win32"} else ".tar.gz"
    artifact = checked_leaf(destination / (stem + suffix))
    metadata = checked_leaf(artifact.with_name(artifact.name + ".json"))
    # The temporary directory is created inside the checked repository output
    # directory. Publication replaces a directory entry, never writes through a
    # raced-in symlink/hardlink. No predictable destination is opened for writing.
    with tempfile.TemporaryDirectory(
        prefix=".dev-setup-archive-", dir=destination
    ) as directory:
        temporary = Path(directory)
        checked_directory(temporary)
        archive_path = temporary / artifact.name
        if sys.platform == "darwin":
            run(
                [
                    "/usr/bin/ditto",
                    "-c",
                    "-k",
                    "--sequesterRsrc",
                    "--keepParent",
                    str(bundle),
                    str(archive_path),
                ]
            )
        elif sys.platform == "win32":
            shutil.make_archive(
                str(temporary / stem),
                "zip",
                root_dir=bundle.parent,
                base_dir=bundle.name,
            )
        else:
            with tarfile.open(archive_path, "w:gz") as archive:
                archive.add(bundle, arcname=bundle.name, recursive=True)
        hasher = hashlib.sha256()
        with archive_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                hasher.update(chunk)
        sidecar = temporary / metadata.name
        sidecar.write_text(
            json.dumps(
                {
                    "version": __version__,
                    "os": host.os.value,
                    "architecture": host.architecture.value,
                    "filename": artifact.name,
                    "size": archive_path.stat().st_size,
                    "sha256": hasher.hexdigest(),
                    "signed": signed,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        checked_leaf(artifact)
        checked_leaf(metadata)
        os.replace(archive_path, artifact)
        os.replace(sidecar, metadata)
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--archive", action="store_true")
    parser.add_argument("--archive-only", action="store_true")
    parser.add_argument(
        "--signed",
        action="store_true",
        help="Require configured native signing/notarization",
    )
    parser.add_argument(
        "--release-version",
        help="Stable version exactly matching the embedded app version",
    )
    args = parser.parse_args()
    work = checked_directory(ROOT / "build" / "pyinstaller-work")
    destination = checked_directory(ROOT / "dist")
    checked_directory(bundle_path())
    if args.signed:
        if not args.archive:
            parser.error(
                "--signed requires --archive so verification cannot be skipped"
            )
        from scripts.release_manifest import release_version
        from scripts.sign_bundle import check_credentials

        release_version(args.release_version or "")
        check_credentials()
        if args.archive_only:
            parser.error("signed builds must rebuild and verify the native bundle")
        os.environ["DEV_SETUP_SIGNED_BUILD"] = "1"
    else:
        os.environ.pop("DEV_SETUP_SIGNED_BUILD", None)
    if not args.archive_only:
        run(
            [
                sys.executable,
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--clean",
                "--workpath",
                str(work),
                "--distpath",
                str(destination),
                str(ROOT / "packaging" / "dev_setup.spec"),
            ]
        )
    if args.smoke_test:
        verify_standalone()
    if args.archive or args.archive_only:
        signed = False
        if args.signed:
            from scripts.sign_bundle import sign_bundle

            signed = sign_bundle(bundle_path(), executable_path())
        artifact = archive_bundle(signed=signed and sys.platform != "darwin")
        if args.signed and sys.platform == "darwin":
            from scripts.sign_bundle import notarize

            notarize(bundle_path(), artifact)
            archive_bundle(signed=True)  # Stapled ticket must be in the final archive.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
