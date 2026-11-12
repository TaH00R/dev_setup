"""Pinned-origin, signed release verification and consent-bound downloads.

No production channel is invented. Signature support is imported lazily, and no
download is ever executed, unpacked, or installed by this module.
"""

import hashlib
import json
import os
import re
import ssl
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Protocol
from urllib.parse import urlsplit

from models.planning import aware_timestamp, utc_now
from models.platform import Platform

MAX_MANIFEST_BYTES = 65536
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024


class UpdateError(ValueError):
    """A bounded, safe error suitable for display without raw transport details."""


def _url(value: str) -> None:
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not parsed.fragment
            and parsed.port in (None, 443)
            and not any(c.isspace() or ord(c) < 32 for c in value)
            and "\\" not in value
            and "/../" not in parsed.path
        )
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise UpdateError(
            "Release URLs must use trusted HTTPS without credentials or fragments."
        )


def _version(value: str) -> tuple[int, int, int, int, int]:
    match = re.fullmatch(
        r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\.dev([0-9]+))?", value
    )
    if match is None or len(value) > 64:
        raise UpdateError("Unsupported application version format.")
    return (
        int(match[1]),
        int(match[2]),
        int(match[3]),
        int(match[4] is None),
        int(match[4] or 0),
    )


@dataclass(frozen=True)
class TrustedChannel:
    manifest_url: str
    signature_url: str
    public_key: bytes = field(repr=False)
    artifact_prefixes: tuple[str, ...] = ()
    minimum_sequence: int = 0

    def __post_init__(self) -> None:
        _url(self.manifest_url)
        _url(self.signature_url)
        if urlsplit(self.manifest_url).netloc != urlsplit(self.signature_url).netloc:
            raise UpdateError(
                "Manifest and signature must share their pinned HTTPS origin."
            )
        if not isinstance(self.public_key, bytes) or len(self.public_key) != 32:
            raise UpdateError("A pinned 32-byte Ed25519 public key is required.")
        if not isinstance(self.artifact_prefixes, tuple) or not self.artifact_prefixes:
            raise UpdateError(
                "Trusted artifact URL prefixes must be explicitly configured."
            )
        for prefix in self.artifact_prefixes:
            _url(prefix)
            if not prefix.endswith("/") or urlsplit(prefix).query:
                raise UpdateError("Artifact prefixes must end at a directory boundary.")
        if type(self.minimum_sequence) is not int or self.minimum_sequence < 0:
            raise UpdateError("Release sequence must be a non-negative integer.")

    def allows(self, url: str) -> bool:
        _url(url)
        return url in (self.manifest_url, self.signature_url) or any(
            url.startswith(prefix) for prefix in self.artifact_prefixes
        )


@dataclass(frozen=True)
class ReleaseArtifact:
    os: str
    architecture: str
    filename: str
    url: str = field(repr=False)
    sha256: str
    size: int


@dataclass(frozen=True)
class Release:
    version: str
    sequence: int
    artifacts: tuple[ReleaseArtifact, ...]


@dataclass(frozen=True)
class UpdateOffer:
    release: Release
    artifact: ReleaseArtifact
    checked_at: datetime
    manifest: bytes = field(repr=False)
    signature: bytes = field(repr=False)


@dataclass(frozen=True)
class UpdateCheck:
    status: str
    message: str
    offer: UpdateOffer | None = None


@dataclass(frozen=True)
class UpdateConsent:
    offer: UpdateOffer = field(repr=False)
    confirmed: bool
    approved_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not isinstance(self.offer, UpdateOffer) or type(self.confirmed) is not bool:
            raise ValueError(
                "update consent requires an exact offer and explicit boolean"
            )
        aware_timestamp(self.approved_at, "update approval time")
        if self.approved_at < self.offer.checked_at:
            raise ValueError("download approval must follow update review")


class UpdateTransport(Protocol):
    def chunks(self, url: str, limit: int, cancel: Event) -> Iterator[bytes]: ...


class _PinnedRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, channel: TrustedChannel) -> None:
        self.channel = channel

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> urllib.request.Request | None:
        if not self.channel.allows(newurl):
            raise UpdateError(
                "A release download redirected outside its pinned HTTPS locations."
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)  # type: ignore[arg-type]


class HTTPSTransport:
    def __init__(self, channel: TrustedChannel) -> None:
        self.channel = channel

    def chunks(self, url: str, limit: int, cancel: Event) -> Iterator[bytes]:
        if not self.channel.allows(url):
            raise UpdateError("The requested release location is not trusted.")
        if cancel.is_set():
            raise UpdateError("Update transfer was cancelled.")
        opener = urllib.request.build_opener(
            _PinnedRedirects(self.channel),
            urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        )
        deadline = time.monotonic() + 120
        try:
            with opener.open(
                urllib.request.Request(
                    url, headers={"User-Agent": "DevSetup-update-check"}
                ),
                timeout=10,
            ) as response:
                if not self.channel.allows(response.geturl()):
                    raise UpdateError(
                        "The release response has an untrusted final origin."
                    )
                length = response.headers.get("Content-Length")
                if length is not None and (not length.isdigit() or int(length) > limit):
                    raise UpdateError(
                        "Release content exceeds its declared transfer bound."
                    )
                total = 0
                while True:
                    if cancel.is_set():
                        raise UpdateError("Update transfer was cancelled.")
                    if time.monotonic() > deadline:
                        raise UpdateError("Update transfer exceeded its time budget.")
                    # HTTPResponse.read(n) can keep filling n bytes indefinitely
                    # under a slow trickle. read1 performs one bounded raw read.
                    chunk = response.read1(65536)
                    if cancel.is_set():
                        raise UpdateError("Update transfer was cancelled.")
                    if time.monotonic() > deadline:
                        raise UpdateError("Update transfer exceeded its time budget.")
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        raise UpdateError(
                            "Release content exceeded its transfer bound."
                        )
                    yield chunk
        except UpdateError:
            raise
        except (OSError, urllib.error.URLError, ValueError):
            raise UpdateError(
                "The verified HTTPS release transfer failed; transport details were omitted."
            ) from None


def verify_manifest(data: bytes, signature: bytes, channel: TrustedChannel) -> Release:
    if (
        not isinstance(data, bytes)
        or len(data) > MAX_MANIFEST_BYTES
        or not isinstance(signature, bytes)
        or len(signature) != 64
    ):
        raise UpdateError("Release metadata or signature has an invalid size.")
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        raise UpdateError(
            "Signed update verification needs the optional cryptography dependency."
        ) from None
    try:
        Ed25519PublicKey.from_public_bytes(channel.public_key).verify(signature, data)
    except (InvalidSignature, ValueError):
        raise UpdateError("Release signature verification failed.") from None

    def pairs(entries: list[tuple[str, object]]) -> dict[str, object]:
        result = {}
        for key, value in entries:
            if key in result:
                raise UpdateError("Release metadata contains duplicate keys.")
            result[key] = value
        return result

    try:
        document = json.loads(data.decode("utf-8"), object_pairs_hook=pairs)
        if (
            not isinstance(document, dict)
            or set(document) != {"schema_version", "version", "sequence", "artifacts"}
            or type(document["schema_version"]) is not int
            or document["schema_version"] != 1
        ):
            raise UpdateError("Release manifest schema is unsupported.")
        version, sequence = document["version"], document["sequence"]
        if not isinstance(version, str) or ".dev" in version:
            raise UpdateError(
                "Only numbered stable releases may be offered as updates."
            )
        _version(version)
        if type(sequence) is not int or sequence < channel.minimum_sequence:
            raise UpdateError("Release sequence is stale or invalid.")
        if (
            not isinstance(document["artifacts"], list)
            or not 1 <= len(document["artifacts"]) <= 12
        ):
            raise UpdateError("Release artifact list is invalid.")
        artifacts = []
        seen = set()
        for item in document["artifacts"]:
            if not isinstance(item, dict) or set(item) != {
                "os",
                "architecture",
                "filename",
                "url",
                "sha256",
                "size",
            }:
                raise UpdateError("Release artifact schema is unsupported.")
            if item["os"] not in {"windows", "macos", "linux"} or item[
                "architecture"
            ] not in {"x86_64", "arm64"}:
                raise UpdateError("Release artifact platform is unsupported.")
            if (
                not isinstance(item["filename"], str)
                or re.fullmatch(
                    r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.(?:zip|tar\.gz|dmg|exe|msi)",
                    item["filename"],
                )
                is None
            ):
                raise UpdateError(
                    "Release filename must be a safe supported archive/installer basename."
                )
            if (
                not isinstance(item["url"], str)
                or not channel.allows(item["url"])
                or item["url"] in (channel.manifest_url, channel.signature_url)
            ):
                raise UpdateError("Release artifact URL is outside pinned locations.")
            if (
                not isinstance(item["sha256"], str)
                or re.fullmatch(r"[a-f0-9]{64}", item["sha256"]) is None
            ):
                raise UpdateError("Release checksum is invalid.")
            if (
                type(item["size"]) is not int
                or not 1 <= item["size"] <= MAX_ARTIFACT_BYTES
            ):
                raise UpdateError("Release artifact size is invalid.")
            key = item["os"], item["architecture"]
            if key in seen:
                raise UpdateError("A release has ambiguous artifacts for one platform.")
            seen.add(key)
            artifacts.append(ReleaseArtifact(**item))
        return Release(version, sequence, tuple(artifacts))
    except UpdateError:
        raise
    except (ValueError, TypeError, KeyError, RecursionError, UnicodeError):
        raise UpdateError(
            "Signed release metadata could not be decoded safely."
        ) from None


class UpdateService:
    def __init__(
        self,
        channel: TrustedChannel | None = None,
        transport: UpdateTransport | None = None,
    ) -> None:
        self.channel = channel
        self.transport = transport or (HTTPSTransport(channel) if channel else None)

    def _read(self, url: str, limit: int, cancel: Event) -> bytes:
        assert self.transport is not None
        value = bytearray()
        for chunk in self.transport.chunks(url, limit, cancel):
            if cancel.is_set():
                raise UpdateError("Update transfer was cancelled.")
            if not isinstance(chunk, bytes) or len(value) + len(chunk) > limit:
                raise UpdateError("Release metadata exceeded its bound.")
            value.extend(chunk)
        return bytes(value)

    def check(
        self,
        current_version: str,
        target: Platform,
        cancel: Event | None = None,
        *,
        current_sequence: int = 0,
    ) -> UpdateCheck:
        if self.channel is None:
            return UpdateCheck(
                "disabled",
                "No trusted signed release channel is configured for this build.",
            )
        cancel = cancel or Event()
        if type(current_sequence) is not int or current_sequence < 0:
            raise UpdateError("The installed release sequence is invalid.")
        manifest = self._read(self.channel.manifest_url, MAX_MANIFEST_BYTES, cancel)
        signature = self._read(self.channel.signature_url, 64, cancel)
        release = verify_manifest(manifest, signature, self.channel)
        if release.sequence < current_sequence:
            raise UpdateError("A signed but stale release sequence was refused.")
        if _version(release.version) <= _version(current_version):
            return UpdateCheck(
                "current", "No newer signed application release is available."
            )
        artifact = next(
            (
                a
                for a in release.artifacts
                if (a.os, a.architecture)
                == (target.os.value, target.architecture.value)
            ),
            None,
        )
        if artifact is None:
            return UpdateCheck(
                "unsupported",
                "The signed release has no artifact for this OS/architecture.",
            )
        return UpdateCheck(
            "available",
            "A newer signed release is available for review.",
            UpdateOffer(release, artifact, utc_now(), manifest, signature),
        )

    def download(
        self,
        offer: UpdateOffer,
        directory: Path,
        consent: UpdateConsent | None,
        cancel: Event | None = None,
    ) -> Path:
        if self.channel is None or self.transport is None:
            raise UpdateError("No trusted signed release channel is configured.")
        if consent is None or not consent.confirmed or consent.offer != offer:
            raise UpdateError(
                "Explicit approval of this exact release download is required."
            )
        release = verify_manifest(offer.manifest, offer.signature, self.channel)
        if release != offer.release or offer.artifact not in release.artifacts:
            raise UpdateError(
                "The reviewed artifact no longer matches its signed release."
            )
        directory = Path(directory)
        if not directory.is_dir() or directory.is_symlink():
            raise UpdateError("Select an existing non-symlink download directory.")
        directory = directory.resolve()
        target = directory / offer.artifact.filename
        if target.exists() or target.is_symlink():
            raise UpdateError("An existing release artifact will not be overwritten.")
        cancel = cancel or Event()
        digest = hashlib.sha256()
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                prefix=".dev-setup-", suffix=".download", dir=directory, delete=False
            ) as stream:
                temporary = Path(stream.name)
                total = 0
                for chunk in self.transport.chunks(
                    offer.artifact.url, offer.artifact.size, cancel
                ):
                    if cancel.is_set():
                        raise UpdateError("Update download was cancelled.")
                    if not isinstance(chunk, bytes):
                        raise UpdateError("Update transport returned invalid content.")
                    total += len(chunk)
                    if total > offer.artifact.size:
                        raise UpdateError("Update download exceeded its signed size.")
                    digest.update(chunk)
                    stream.write(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            if (
                total != offer.artifact.size
                or digest.hexdigest() != offer.artifact.sha256
            ):
                raise UpdateError(
                    "Update download failed its signed size/checksum verification."
                )
            if cancel.is_set():
                raise UpdateError("Update download was cancelled.")
            # Hard-link publication is atomic and refuses overwriting a raced-in
            # existing target on all supported filesystems. No archive is run.
            os.link(temporary, target)
            return target
        except UpdateError:
            raise
        except OSError:
            raise UpdateError(
                "The verified release artifact could not be saved without overwriting existing data."
            ) from None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    # Do not replace the verified result with raw cleanup errors.
                    # A failed cleanup can leave only an unexecuted .download file.
                    pass
