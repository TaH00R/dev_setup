"""Link decoded native package indexes to SHA-256 entries in verified releases."""

import hashlib
import json
import re
import stat
from dataclasses import replace
from pathlib import Path, PurePosixPath
from threading import Event

from models.errors import ErrorCode
from models.specs import InstallSpec
from system.identity import IdentityError, bind, revalidate
from system.runner import CommandRunner

from .base import AdapterError, BackendAction, PackageManager


def _fail() -> AdapterError:
    return AdapterError(
        ErrorCode.VERIFICATION_FAILED,
        "Cached APT package-index integrity could not be verified.",
    )


def release_checksums(content: bytes) -> dict[str, tuple[str, int]]:
    """Only call with bytes whose cleartext signature was already authenticated."""
    try:
        _, separator, body = content.partition(b"\n\n")
        body, signature, _ = body.partition(b"\n-----BEGIN PGP SIGNATURE-----")
        if not separator or not signature:
            raise ValueError
        rows = body.decode("utf-8").splitlines()
        result = {}
        active = False
        seen = False
        for line in rows:
            if line == "SHA256:":
                if seen:
                    raise ValueError
                seen = active = True
            elif active and line.startswith((" ", "\t")):
                fields = line.split()
                if len(fields) != 3:
                    raise ValueError
                digest, size, name = fields
                path = PurePosixPath(name)
                if (
                    re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None
                    or re.fullmatch(r"[0-9]{1,20}", size) is None
                    or path.is_absolute()
                    or ".." in path.parts
                    or any(ord(c) < 32 for c in name)
                    or name in result
                ):
                    raise ValueError
                result[name] = (digest.lower(), int(size))
                if len(result) > 32768:
                    raise ValueError
            else:
                active = False
        if not seen or not result:
            raise ValueError
        return result
    except (ValueError, UnicodeError):
        raise _fail() from None


def index_fingerprint(
    adapter: PackageManager,
    runner: CommandRunner,
    spec: InstallSpec,
    indexes: tuple[tuple[Path, str, Path, Path], ...],
    releases: dict[Path, bytes],
    cancel: Event | None,
    *,
    list_roots: tuple[Path, ...],
    helper_path: Path,
) -> str:
    if not helper_path.is_absolute() or not helper_path.is_file():
        raise AdapterError(
            ErrorCode.MISSING_BACKEND,
            "APT index verification requires native apt-helper.",
        )
    checksums = {path: release_checksums(content) for path, content in releases.items()}
    records = []
    total = 0
    for index, meta, release, _ in indexes:
        expected = checksums.get(release, {}).get(meta)
        if expected is None or not 0 <= expected[1] <= 512 * 1024 * 1024:
            raise _fail()
        total += expected[1]
        if total > 2 * 1024 * 1024 * 1024:
            raise _fail()
        try:
            if (
                not index.is_absolute()
                or ".." in index.parts
                or index.is_symlink()
                or not any(
                    index.resolve(strict=True).is_relative_to(root.resolve())
                    for root in list_roots
                )
                or not stat.S_ISREG(index.stat().st_mode)
                or index.stat().st_size > 256 * 1024 * 1024
            ):
                raise _fail()
            command = bind(
                adapter.request(
                    (
                        str(helper_path),
                        "-o",
                        "Apt-Helper::Cat-File::Compress=",
                        "cat-file",
                        "--",
                        str(index),
                    ),
                    BackendAction.SHOW,
                    spec,
                    environment=(("LC_ALL", "C"),),
                    launch_paths=(str(index),),
                )
            )
            command = replace(command, stdout_sha256_limit=expected[1])
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
                or result.stdout_sha256 != expected[0]
                or result.stdout_size != expected[1]
            ):
                raise _fail()
            revalidate(command, environment=dict(command.environment))
        except (OSError, IdentityError):
            raise _fail() from None
        records.append(
            (
                meta,
                expected,
                tuple(
                    (file.requested, file.resolved, file.digest)
                    for file in command.launch_files
                ),
            )
        )
    return hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
