"""Read-only installed-package readers; never used to authorize mutations.

Repository provenance is unknown unless explicitly represented by the native data.
Malformed, failed or truncated reads are errors, never empty installation evidence.
"""

import json
import re
import tempfile
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import Any

from models.errors import ErrorCode
from models.inventory import InstalledPackage, Inventory
from system.commands import Command
from system.runner import CommandRunner

from .base import AdapterError, PackageManager
from .inspection import NativeInspector


def invalid() -> AdapterError:
    return AdapterError(
        ErrorCode.VERIFICATION_FAILED,
        "Installed-package data is incomplete or has an unsupported format.",
    )


def text(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise invalid()
    return value.strip()


def object_value(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise invalid()
    return value


def array(value: object) -> list[Any]:
    if not isinstance(value, list):
        raise invalid()
    return value


def parse_json(output: str) -> dict[str, Any]:
    def unique(entries: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in entries:
            if key in result:
                raise invalid()
            result[key] = value
        return result

    try:
        return object_value(json.loads(output, object_pairs_hook=unique))
    except (ValueError, RecursionError) as error:
        raise invalid() from error


def records(backend: str, rows: list[tuple[str, str | None]]) -> Inventory:
    grouped: dict[str, list[str]] = defaultdict(list)
    for name, version in rows:
        name = text(name)
        if version is not None:
            version = text(version)
            if version not in grouped[name]:
                grouped[name].append(version)
        else:
            grouped[name]
    return Inventory(
        backend,
        tuple(
            InstalledPackage(name, tuple(versions))
            for name, versions in sorted(grouped.items())
        ),
    )


class ReadOnlyInventory(NativeInspector):
    """Adapter-owned native queries, separate from the reviewed operation inspectors."""

    def __init__(self, adapter: PackageManager, runner: CommandRunner) -> None:
        super().__init__(adapter, runner)

    def inventory(self, cancel: Event | None = None) -> Inventory:
        if cancel and cancel.is_set():
            raise AdapterError(ErrorCode.CANCELLED, "Inventory refresh was cancelled.")
        try:
            return self._inventory(cancel)
        except AdapterError as error:
            if error.error.code == ErrorCode.TIMEOUT:
                raise AdapterError(
                    ErrorCode.TIMEOUT,
                    "The installed-package query timed out; other inventory sources remain available.",
                ) from error
            raise
        except (ValueError, TypeError, KeyError, OSError, RecursionError) as error:
            raise invalid() from error

    def _inventory(self, cancel: Event | None) -> Inventory:
        backend = self.adapter.id
        command = replace(self.adapter.list_installed(), environment=())
        if backend == "winget":
            # Export is structured and language independent. It only includes apps
            # matched by WinGet; Windows registry inventory supplements omissions.
            with tempfile.TemporaryDirectory(prefix="dev-setup-winget-") as directory:
                target = Path(directory) / "installed.json"
                query = replace(
                    command,
                    argv=(
                        command.argv[0],
                        "export",
                        "--output",
                        str(target),
                        "--include-versions",
                        "--source",
                        "winget",
                        "--disable-interactivity",
                    ),
                )
                self.read(query, cancel)
                if (
                    target.is_symlink()
                    or not target.is_file()
                    or target.stat().st_size > 8 * 1024 * 1024
                ):
                    raise invalid()
                data = parse_json(target.read_text(encoding="utf-8-sig"))
            rows = []
            for source in array(data.get("Sources")):
                source = object_value(source)
                for package in array(source.get("Packages")):
                    package = object_value(package)
                    rows.append(
                        (
                            text(package.get("PackageIdentifier")),
                            text(package["Version"])
                            if package.get("Version")
                            else None,
                        )
                    )
            return records(backend, rows)
        if backend == "scoop":
            arguments = list(command.argv)
            arguments[arguments.index("-Action") + 1] = "inventory-json"
            data = parse_json(
                self.read(replace(command, argv=tuple(arguments)), cancel)
            )
            rows = []
            for raw in array(data.get("apps")):
                package = object_value(raw)
                # Keep arbitrary bucket names as display data, never catalog sources.
                name = text(package.get("Name"))
                origin = (
                    text(package.get("Source")) if package.get("Source") else "unknown"
                )
                scope = (
                    "global"
                    if "global" in str(package.get("Info", "")).lower()
                    else "user"
                )
                rows.append(
                    (f"{origin}/{name} [{scope}]", text(package.get("Version")))
                )
            return records(backend, rows)
        if backend in {"dnf", "zypper"}:
            # Both use the local RPM database; no repository refresh/plugins needed.
            rpm = self.adapter.find("rpm")
            if not rpm or not Path(rpm).is_absolute():
                raise AdapterError(
                    ErrorCode.MISSING_BACKEND,
                    "The local RPM query executable is unavailable.",
                )
            query = Command(
                (
                    rpm,
                    "-qa",
                    "--queryformat",
                    r"%{NAME}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}\t%{ARCH}\n",
                ),
                "Read the local RPM installation database",
                self.adapter.os,
                backend,
            )
            output = self.read(query, cancel)
            rows = []
            for line in output.splitlines():
                name, version, arch = line.split("\t")
                rows.append((f"{text(name)}.{text(arch)}", text(version)))
            return records(backend, rows)
        if backend == "pacman":
            output = self.read(command, cancel)
            rows = []
            for line in output.splitlines():
                name, version = line.split()
                rows.append((name, version))
            return records(backend, rows)
        if backend == "apk":
            command = replace(
                command, argv=(command.argv[0], "--no-network", "info", "--verbose")
            )
            output = self.read(command, cancel)
            rows = []
            for line in output.splitlines():
                match = re.fullmatch(r"(.+)-([0-9][^\s]*)", line)
                if match is None:
                    raise invalid()
                rows.append((match[1], match[2]))
            return records(backend, rows)
        if backend == "flatpak":
            # Include user and system apps. Full refs retain architecture/branch;
            # installation and origin keep side-by-side records unambiguous.
            query = replace(
                command,
                argv=(
                    command.argv[0],
                    "list",
                    "--app",
                    "--columns=ref:full,version:full,origin:full,installation:full",
                ),
            )
            rows = []
            for line in self.read(query, cancel).splitlines():
                reference, version, origin, installation = line.split("\t")
                rows.append(
                    (
                        f"{text(reference)} [{text(installation)}; {text(origin) if origin else 'unknown'}]",
                        version or None,
                    )
                )
            return records(backend, rows)
        raise AdapterError(
            ErrorCode.UNSUPPORTED_OPERATION,
            "No read-only inventory reader is registered for this backend.",
        )
