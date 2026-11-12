"""Native signing on provisioned release hosts; credentials never enter argv."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value or len(value) > 256 or any(ord(c) < 32 for c in value):
        raise ValueError(f"Signed release requires configured {name}.")
    return value


def check_credentials() -> None:
    if sys.platform == "win32":
        if (
            re.fullmatch(r"[0-9a-fA-F]{40}", required("DEV_SETUP_WINDOWS_CERT_SHA1"))
            is None
        ):
            raise ValueError(
                "Windows signing requires an exact certificate thumbprint."
            )
        timestamp = urlsplit(required("DEV_SETUP_TIMESTAMP_URL"))
        if (
            timestamp.scheme != "https"
            or not timestamp.hostname
            or timestamp.username
            or timestamp.password
            or timestamp.fragment
        ):
            raise ValueError(
                "The timestamp authority must be a configured HTTPS origin."
            )
        if not shutil.which("signtool"):
            raise ValueError(
                "Signed Windows release requires Windows SDK signtool on PATH."
            )
    elif sys.platform == "darwin":
        identity = required("DEV_SETUP_MAC_SIGN_IDENTITY")
        if not identity.startswith("Developer ID Application:") or any(
            c in identity for c in '\\"'
        ):
            raise ValueError(
                "macOS release requires a Developer ID Application identity."
            )
        required("DEV_SETUP_MAC_NOTARY_PROFILE")
        if not Path("/usr/bin/codesign").is_file() or not shutil.which("xcrun"):
            raise ValueError("Signed macOS release requires Apple command-line tools.")
    elif sys.platform != "linux":
        raise ValueError("This platform has no configured release-signing procedure.")


def run(arguments: list[str], *, timeout: int = 600) -> None:
    subprocess.run(arguments, check=True, timeout=timeout, shell=False)


def sign_bundle(bundle: Path, executable: Path) -> bool:
    check_credentials()
    if sys.platform == "win32":
        tool = shutil.which("signtool")
        assert tool is not None
        run(
            [
                tool,
                "sign",
                "/sha1",
                required("DEV_SETUP_WINDOWS_CERT_SHA1"),
                "/fd",
                "SHA256",
                "/tr",
                required("DEV_SETUP_TIMESTAMP_URL"),
                "/td",
                "SHA256",
                str(executable),
            ]
        )
        run([tool, "verify", "/pa", "/all", str(executable)])
        return True
    if sys.platform == "darwin":
        # PyInstaller signs the nested binaries and bundle at build time with
        # hardened runtime. Verify that result before notarizing the archive.
        requirement = (
            'anchor apple generic and certificate leaf[subject.CN] = "'
            + required("DEV_SETUP_MAC_SIGN_IDENTITY")
            + '"'
        )
        run(
            [
                "/usr/bin/codesign",
                "--verify",
                "--deep",
                "--strict",
                "-R",
                requirement,
                str(bundle),
            ]
        )
        return True
    return False  # Linux artifacts receive the detached release signature.


def notarize(bundle: Path, artifact: Path) -> None:
    if sys.platform != "darwin":
        return
    check_credentials()
    run(
        [
            "xcrun",
            "notarytool",
            "submit",
            str(artifact),
            "--keychain-profile",
            required("DEV_SETUP_MAC_NOTARY_PROFILE"),
            "--wait",
            "--timeout",
            "20m",
        ],
        timeout=1500,
    )
    run(["xcrun", "stapler", "staple", str(bundle)])
    run(["xcrun", "stapler", "validate", str(bundle)])
    run(["/usr/sbin/spctl", "--assess", "--type", "execute", str(bundle)])
