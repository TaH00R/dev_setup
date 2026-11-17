"""Offline authentication of cached InRelease files; no refresh or package writes.

Package-index checksums and release/key freshness are separate checks, not implied
by a cryptographically valid signature against an explicitly configured keyring.
"""

import hashlib
import json
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from threading import Event

from models.errors import ErrorCode
from models.planning import utc_now
from models.specs import InstallSpec
from system.identity import IdentityError, bind, revalidate
from system.runner import CommandRunner

from .apt_freshness import validate_freshness
from .apt_indexes import index_fingerprint
from .apt_trust import _keys, _read
from .base import AdapterError, BackendAction, PackageManager


def _fail() -> AdapterError:
    return AdapterError(
        ErrorCode.VERIFICATION_FAILED,
        "Cached APT release authentication failed or its layout/key scope is unsupported.",
    )


def cached_indexes(entries: str) -> tuple[tuple[Path, str, Path, Path], ...]:
    """Map reviewed component/binary index names back to their cached release."""
    rows = entries.splitlines()
    if not rows or len(rows) > 512:
        raise _fail()
    indexes = set()
    for row in rows:
        fields = row.split("|")
        if len(fields) != 3:
            raise _fail()
        filename, meta, entry = fields
        source = re.fullmatch(r"([^\x00-\x1f]+\.(?:list|sources)):[1-9][0-9]*", entry)
        if (
            source is None
            or re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9+._-]*/binary-(?:amd64|arm64|i386|armhf|all)/Packages",
                meta,
            )
            is None
        ):
            raise _fail()
        index = Path(filename)
        name = re.sub(r"\.(?:lz4|gz|xz)$", "", index.name)
        suffix = "_" + meta.replace("/", "_")
        if not index.is_absolute() or not name.endswith(suffix):
            raise _fail()
        prefix = name[: -len(suffix)]
        if re.fullmatch(r".+_dists_[A-Za-z0-9][A-Za-z0-9+._-]*", prefix) is None:
            raise _fail()
        indexes.add(
            (index, meta, index.with_name(prefix + "_InRelease"), Path(source[1]))
        )
    return tuple(sorted(indexes))


def cached_releases(entries: str) -> tuple[tuple[Path, Path], ...]:
    releases = {(release, source) for _, _, release, source in cached_indexes(entries)}
    if len(releases) > 128:
        raise _fail()
    return tuple(sorted(releases))


def valid_status(output: str) -> bool:
    valid = False
    allowed = {
        "NEWSIG",
        "KEY_CONSIDERED",
        "SIG_ID",
        "GOODSIG",
        "VALIDSIG",
        "PLAINTEXT",
        "VERIFICATION_COMPLIANCE_MODE",
    }
    for line in output.splitlines():
        if not line.startswith("[GNUPG:] "):
            return False
        fields = line[9:].split()
        if not fields or fields[0] not in allowed:
            return False
        if fields[0] == "VALIDSIG":
            fingerprint = r"(?:[0-9A-F]{40}|[0-9A-F]{64})"
            if (
                len(fields) not in {10, 11}
                or re.fullmatch(fingerprint, fields[1]) is None
                or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", fields[2]) is None
                or any(
                    not field.isascii() or not field.isdigit() for field in fields[3:9]
                )
                or fields[8] not in {"8", "9", "10"}
                or fields[9] not in {"00", "01"}
                or (len(fields) == 11 and re.fullmatch(fingerprint, fields[10]) is None)
            ):
                return False
            valid = True
    return valid


def cache_fingerprint(
    adapter: PackageManager,
    runner: CommandRunner,
    spec: InstallSpec,
    entries: str,
    cancel: Event | None,
    *,
    source_roots: tuple[Path, ...],
    key_roots: tuple[Path, ...],
    list_roots: tuple[Path, ...],
    helper_path: Path = Path("/usr/lib/apt/apt-helper"),
    clock: Callable[[], datetime] = utc_now,
) -> str:
    verifier = adapter.find("gpgv")
    if verifier is None or not Path(verifier).is_absolute():
        raise AdapterError(
            ErrorCode.MISSING_BACKEND, "APT signed-cache review requires native gpgv."
        )
    records = []
    release_content = {}
    total = 0
    for release, source in cached_releases(entries):
        manifest = _read(source, source_roots)
        keys = _keys(source, manifest, uniform=True)
        if any(key.suffix != ".gpg" for key in keys):
            raise _fail()
        content = _read(release, list_roots)
        if not content.startswith(b"-----BEGIN PGP SIGNED MESSAGE-----\n"):
            raise _fail()
        snapshots = {str(release): hashlib.sha256(content).hexdigest()}
        total += len(content) + len(manifest)
        for key in keys:
            data = _read(key, key_roots)
            total += len(data)
            snapshots[str(key)] = hashlib.sha256(data).hexdigest()
            if total > 16 * 1024 * 1024:
                raise _fail()
        if total > 16 * 1024 * 1024:
            raise _fail()
        argv: tuple[str, ...] = (verifier, "--status-fd", "1", "--weak-digest", "SHA1")
        for key in keys:
            argv += ("--keyring", str(key))
        argv += ("--", str(release))
        try:
            command = bind(
                adapter.request(
                    argv,
                    BackendAction.SHOW,
                    spec,
                    environment=(("LC_ALL", "C"),),
                    launch_paths=(str(release), *(str(key) for key in keys)),
                )
            )
            if any(
                file.digest != snapshots[file.requested]
                for file in command.launch_files
                if file.requested in snapshots
            ):
                raise _fail()
            result = runner.run(command, cancel=cancel)
            if result.error and result.error.code in {
                ErrorCode.CANCELLED,
                ErrorCode.TIMEOUT,
                ErrorCode.PROCESS_CONTROL,
            }:
                raise AdapterError(result.error.code, result.error.message)
            if (
                not result.succeeded
                or result.output_truncated
                or not valid_status(result.stdout)
            ):
                raise _fail()
            revalidate(command, environment=dict(command.environment))
            validate_freshness(content, clock())
        except IdentityError:
            raise _fail() from None
        records.append(
            (
                command.argv,
                tuple(
                    (file.requested, file.resolved, file.digest)
                    for file in command.launch_files
                ),
            )
        )
        release_content[release] = content
    indexes = index_fingerprint(
        adapter,
        runner,
        spec,
        cached_indexes(entries),
        release_content,
        cancel,
        list_roots=list_roots,
        helper_path=helper_path,
    )
    for content in release_content.values():
        validate_freshness(content, clock())
    return hashlib.sha256(
        json.dumps((records, indexes), sort_keys=True).encode()
    ).hexdigest()
