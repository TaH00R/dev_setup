"""Bounded identity of native APT source manifests and explicit public keyrings.

This detects configuration drift; it does not authenticate a key or a signature.
Only explicit keyring paths in the reviewed directories are supported for now.
"""

import hashlib
import json
import os
import re
import stat
from pathlib import Path

from models.errors import ErrorCode

from .base import AdapterError


def _fail() -> AdapterError:
    return AdapterError(
        ErrorCode.VERIFICATION_FAILED,
        "APT source/key identity is unavailable, changed during review or outside the reviewed explicit-keyring scope.",
    )


def _read(path: Path, roots: tuple[Path, ...]) -> bytes:
    """Reject links, special files, escaping paths and concurrent changes."""
    try:
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError
        resolved = path.resolve(strict=True)
        if path.is_symlink() or not any(
            resolved.is_relative_to(root.resolve()) for root in roots
        ):
            raise ValueError
        initial = path.stat()
        if not stat.S_ISREG(initial.st_mode) or initial.st_size > 2 * 1024 * 1024:
            raise ValueError
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError
            content = stream.read(2 * 1024 * 1024 + 1)
            after = os.fstat(stream.fileno())

        def identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
            return (
                value.st_dev,
                value.st_ino,
                value.st_size,
                value.st_mtime_ns,
                getattr(value, "st_birthtime_ns", value.st_ctime_ns)
                if os.name == "nt"
                else value.st_ctime_ns,
            )

        if (
            len(content) > 2 * 1024 * 1024
            or any(
                identity(value) != identity(before)
                for value in (initial, after, path.stat())
            )
            or path.is_symlink()
            or path.resolve(strict=True) != resolved
        ):
            raise ValueError
        return content
    except (OSError, ValueError):
        raise _fail() from None


def _keys(path: Path, content: bytes) -> tuple[Path, ...]:
    try:
        text = content.decode("utf-8")
    except UnicodeError:
        raise _fail() from None
    values = []
    if path.suffix == ".sources":
        # Unfold Deb822 fields, preserving stanza boundaries. Native APT still
        # parses the actual sources; this reader only bounds the trust inputs.
        for paragraph in re.split(r"\n[ \t]*\n", text.replace("\r\n", "\n")):
            lines = [
                line
                for line in paragraph.splitlines()
                if line.strip() and not line.startswith("#")
            ]
            if not lines:
                continue
            unfolded = re.sub(r"\n[ \t]+", " ", "\n".join(lines))
            if re.search(r"(?im)^Enabled:[ \t]*no[ \t]*$", unfolded):
                continue
            matches = re.findall(r"(?im)^Signed-By:[ \t]*([^\n]*)$", unfolded)
            if len(matches) != 1:
                raise _fail()
            values.append(matches[0].split())
    elif path.suffix == ".list":
        for line in text.splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            if not re.match(r"deb(?:-src)?\s", line):
                raise _fail()
            matches = re.findall(r"(?i)(?:\[|\s)signed-by=([^\]\s]+)", line)
            if len(matches) != 1:
                raise _fail()
            values.append(matches[0].split(","))
    else:
        raise _fail()
    if not values or any(not tokens for tokens in values):
        raise _fail()
    keys = tuple(dict.fromkeys(Path(token) for tokens in values for token in tokens))
    if len(keys) > 128 or any(
        not key.is_absolute() or key.suffix not in {".gpg", ".asc"} for key in keys
    ):
        # Global trust, fingerprint-only selectors and inline keys need their own
        # reviewed implementation; never silently omit them from the snapshot.
        raise _fail()
    return keys


def trust_fingerprint(
    entries: str,
    *,
    source_roots: tuple[Path, ...] = (Path("/etc/apt"),),
    key_roots: tuple[Path, ...] = (
        Path("/etc/apt/keyrings"),
        Path("/usr/share/keyrings"),
    ),
) -> str:
    """Snapshot paths reported by native indextargets, never catalog-provided paths."""
    sources = set()
    rows = entries.splitlines()
    if not rows or len(rows) > 512:
        raise _fail()
    for row in rows:
        match = re.fullmatch(r"([^\x00-\x1f]+\.(?:list|sources)):([1-9][0-9]*)", row)
        if match is None:
            raise _fail()
        sources.add(Path(match[1]))
    records = {}
    total = 0
    for source in sorted(sources):
        content = _read(source, source_roots)
        total += len(content)
        records[str(source)] = hashlib.sha256(content).hexdigest()
        for key in _keys(source, content):
            if str(key) in records:
                continue
            data = _read(key, key_roots)
            total += len(data)
            records[str(key)] = hashlib.sha256(data).hexdigest()
            if total > 16 * 1024 * 1024 or len(records) > 128:
                raise _fail()
        if total > 16 * 1024 * 1024 or len(records) > 128:
            raise _fail()
    return hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest()
