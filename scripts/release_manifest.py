"""Produce an explicitly signed stable release feed from verified native archives."""

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import __version__  # noqa: E402
from services.updates import TrustedChannel, verify_manifest  # noqa: E402


def release_version(expected: str) -> str:
    if (
        expected != __version__
        or re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", expected)
        is None
    ):
        raise ValueError(
            "A stable release tag/version must match app.__version__ exactly."
        )
    return expected


def produce(
    directory: Path, version: str, sequence: int, base_url: str
) -> tuple[bytes, bytes]:
    release_version(version)
    if type(sequence) is not int or sequence < 1:
        raise ValueError(
            "A positive, monotonically increasing release sequence is required."
        )
    parsed = urlsplit(base_url)
    if not base_url.endswith("/") or parsed.query:
        raise ValueError("The release base URL must end at an HTTPS directory.")
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    # Raw 32-byte key supplied by protected CI secret; never write or log it.
    try:
        private = Ed25519PrivateKey.from_private_bytes(
            bytes.fromhex(os.environ.get("DEV_SETUP_RELEASE_SIGNING_KEY", ""))
        )
    except ValueError:
        raise ValueError(
            "A protected 32-byte Ed25519 release signing key is required."
        ) from None
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    channel = TrustedChannel(
        base_url + "release.json",
        base_url + "release.sig",
        public,
        (base_url,),
        sequence,
    )
    artifacts = []
    for metadata in sorted(directory.glob("*.json")):
        if metadata.name == "release.json":
            continue
        if metadata.is_symlink() or metadata.stat().st_size > 4096:
            raise ValueError("Release metadata must be bounded regular files.")
        item = json.loads(metadata.read_text(encoding="utf-8"))
        if not isinstance(item, dict) or set(item) != {
            "version",
            "os",
            "architecture",
            "filename",
            "sha256",
            "size",
            "signed",
        }:
            raise ValueError("Release metadata schema is invalid.")
        if (
            item["version"] != version
            or Path(item["filename"]).name != item["filename"]
        ):
            raise ValueError(
                "Artifact version or filename does not match this release."
            )
        path = directory / item["filename"]
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != item["size"]
        ):
            raise ValueError("A release artifact changed after the native build.")
        if item["os"] in {"windows", "macos"} and item["signed"] is not True:
            raise ValueError(
                "Windows/macOS signed releases require verified native signing."
            )
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                hasher.update(chunk)
        if hasher.hexdigest() != item["sha256"]:
            raise ValueError("A release artifact failed its build checksum.")
        artifacts.append(
            {
                key: item[key]
                for key in ("os", "architecture", "filename", "sha256", "size")
            }
            | {"url": base_url + item["filename"]}
        )
    if {item["os"] for item in artifacts} != {"windows", "macos", "linux"}:
        raise ValueError("A stable release requires Windows, macOS and Linux archives.")
    manifest = json.dumps(
        {
            "schema_version": 1,
            "version": version,
            "sequence": sequence,
            "artifacts": artifacts,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    signature = private.sign(manifest)
    verify_manifest(manifest, signature, channel)
    return manifest, signature


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--sequence", type=int, required=True)
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    manifest, signature = produce(
        args.directory, args.version, args.sequence, args.base_url
    )
    for name, content in (("release.json", manifest), ("release.sig", signature)):
        with (args.directory / name).open("xb") as stream:
            stream.write(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
