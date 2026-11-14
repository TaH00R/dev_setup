"""Snapshot launch files and their resolved targets without executing anything."""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING

from models.platform import OperatingSystem

if TYPE_CHECKING:
    from .commands import Command


class IdentityError(ValueError):
    """Safe fixed message; paths and OS exception details are not displayed."""


@dataclass(frozen=True)
class LaunchFile:
    requested: str
    resolved: str
    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int
    digest: str
    header: bytes = field(default=b"", repr=False)


def capture(path: str) -> LaunchFile:
    try:
        requested = Path(path)
        if not requested.is_absolute():
            raise ValueError("absolute launch files required")
        resolved = requested.resolve(strict=True)
        if not stat.S_ISREG(resolved.stat().st_mode):
            raise ValueError("unsupported launch file")
        descriptor = os.open(
            resolved,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > 256 * 1024 * 1024:
                raise ValueError("unsupported launch file")
            digest = hashlib.sha256()
            total = 0
            header = b""
            while block := stream.read(1024 * 1024):
                if total == 0:
                    header = block[:512]
                total += len(block)
                if total > 256 * 1024 * 1024:
                    raise ValueError("launch file grew beyond its bound")
                digest.update(block)
            after = os.fstat(stream.fileno())

        def signature(value: os.stat_result) -> tuple[int, int, int, int, int]:
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
            signature(before) != signature(after)
            or requested.resolve(strict=True) != resolved
            or signature(resolved.stat()) != signature(after)
        ):
            raise ValueError("launch identity changed during inspection")
        return LaunchFile(
            str(requested), str(resolved), *signature(after), digest.hexdigest(), header
        )
    except (OSError, ValueError):
        raise IdentityError(
            "A launch file could not be identified safely. Review a fresh plan after checking the installed executable."
        ) from None


def _files(
    command: Command, environment: Mapping[str, str] | None = None
) -> tuple[LaunchFile, ...]:
    paths = [command.argv[0], *command.launch_paths]
    snapshots = {path: capture(path) for path in dict.fromkeys(paths)}
    if command.target_os != OperatingSystem.WINDOWS:
        # POSIX scripts delegate to a shebang interpreter. Pin that chain too.
        executable = command.argv[0]
        seen = set()
        while executable not in seen:
            seen.add(executable)
            if len(seen) > 4:
                raise IdentityError(
                    "The executable interpreter chain is unsupported; review is required."
                )
            try:
                if executable not in snapshots:
                    snapshots[executable] = capture(executable)
                header = snapshots[executable].header
                if not header.startswith(b"#!"):
                    break
                tokens = shlex.split(header.split(b"\n", 1)[0][2:].decode("utf-8"))
                if not tokens or not Path(tokens[0]).is_absolute():
                    raise ValueError("unsupported interpreter")
                paths.append(tokens[0])
                executable = tokens[0]
                if Path(executable).name == "env":
                    if (
                        len(tokens) != 2
                        or tokens[1].startswith("-")
                        or "/" in tokens[1]
                    ):
                        raise ValueError("unsupported env interpreter")
                    inherited = os.environ if environment is None else environment
                    path = dict(command.environment).get(
                        "PATH", inherited.get("PATH", os.defpath)
                    )
                    selected = shutil.which(tokens[1], path=path)
                    if selected is None:
                        raise ValueError("missing interpreter")
                    paths.append(selected)
                    executable = selected
            except (OSError, UnicodeError, ValueError):
                raise IdentityError(
                    "The executable interpreter could not be reviewed safely."
                ) from None
    for path in dict.fromkeys(paths):
        if path not in snapshots:
            snapshots[path] = capture(path)
        if capture(path) != snapshots[path]:
            raise IdentityError(
                "Launch identity changed during review. A fresh review is required."
            )
    return tuple(snapshots[path] for path in dict.fromkeys(paths))


def bind(command: Command) -> Command:
    return replace(command, launch_files=_files(command))


def revalidate(
    command: Command, *, environment: Mapping[str, str] | None = None
) -> None:
    if not command.launch_files or _files(command, environment) != command.launch_files:
        raise IdentityError(
            "Executable, interpreter or bridge identity changed after review. A fresh review and approval are required."
        )
