"""Reviewed native runtime adapters; no bootstrap scripts or shell plugins."""

import json
import os
import re
import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from models.environment import RuntimeRequest, exact_version
from models.errors import ErrorCode
from models.platform import Architecture, LinuxFamily, OperatingSystem, Platform
from package_managers.base import (
    AdapterError,
    Availability,
    BackendAction,
    Capabilities,
    Finder,
)
from system.commands import Command


class RuntimeManager(ABC):
    id: str
    runtimes: tuple[str, ...]
    capabilities = Capabilities(
        frozenset({BackendAction.VERSION, BackendAction.LIST, BackendAction.INSTALL}),
        frozenset({BackendAction.INSTALL}),
    )

    def __init__(self, host: Platform, *, find: Finder | None = None) -> None:
        self.host = host
        self.find = find or shutil.which

    def supports_host(self) -> bool:
        return self.host.unsupported_reason is None and self.host.architecture in {
            Architecture.X86_64,
            Architecture.ARM64,
        }

    def availability(self) -> Availability:
        if not self.supports_host():
            return Availability(
                self.id,
                False,
                reason="Runtime adapter requires a recognized 64-bit platform.",
            )
        name = self.id + (".exe" if self.host.os == OperatingSystem.WINDOWS else "")
        path = self.find(name)
        if (
            path is None
            or not Path(path).is_absolute()
            or Path(path).suffix.lower() in {".cmd", ".bat", ".ps1"}
        ):
            return Availability(
                self.id,
                False,
                reason="An existing native runtime manager executable is required.",
            )
        return Availability(self.id, True, ((name, path),))

    def _command(self, arguments: tuple[str, ...], *, mutates: bool = False) -> Command:
        available = self.availability()
        if not available.available:
            raise AdapterError(
                ErrorCode.MISSING_BACKEND,
                available.reason or "Runtime manager unavailable.",
            )
        return Command(
            (available.executables[0][1], *arguments),
            f"{self.id}: {'install runtime' if mutates else 'inspect installed runtimes'}",
            self.host.os,
            f"runtime-{self.id}",
            mutates=mutates,
            timeout_seconds=900 if mutates else 30,
        )

    def validate(self, request: RuntimeRequest) -> None:
        if not self.supports_host():
            raise AdapterError(
                ErrorCode.UNSUPPORTED_PLATFORM,
                "Runtime adapter requires a recognized 64-bit platform.",
            )
        if request.manager != self.id or request.runtime not in self.runtimes:
            raise AdapterError(
                ErrorCode.UNSUPPORTED_OPERATION,
                "This manager/runtime combination has no reviewed adapter.",
            )

    def version(self) -> Command:
        return self._command(("--version",))

    @abstractmethod
    def install(self, request: RuntimeRequest) -> Command:
        """Construct a version-pinned manager-owned installation."""

    @abstractmethod
    def inventory(self, request: RuntimeRequest) -> Command:
        """Construct read-only native inventory."""

    @abstractmethod
    def parse_inventory(self, output: str) -> tuple[str, ...]:
        """Return exact installed versions or fail closed."""

    @staticmethod
    def _rows(output: str) -> list[dict[str, Any]]:
        try:
            rows = json.loads(output)
            if (
                not isinstance(rows, list)
                or len(rows) > 10000
                or any(not isinstance(row, dict) for row in rows)
            ):
                raise ValueError()
            return rows
        except (ValueError, TypeError, RecursionError) as exc:
            raise AdapterError(
                ErrorCode.VERIFICATION_FAILED,
                "Native runtime inventory was malformed or unsupported.",
            ) from exc


class Mise(RuntimeManager):
    id = "mise"
    runtimes = ("python", "node", "go")
    flags = ("--no-config", "--no-env", "--no-hooks")

    def install(self, request: RuntimeRequest) -> Command:
        self.validate(request)
        return self._command(
            (*self.flags, "install", "--", f"{request.runtime}@{request.version}"),
            mutates=True,
        )

    def inventory(self, request: RuntimeRequest) -> Command:
        self.validate(request)
        return self._command(
            (*self.flags, "ls", "--installed", "--json", "--", request.runtime)
        )

    def parse_inventory(self, output: str) -> tuple[str, ...]:
        versions = set()
        for row in self._rows(output):
            version = row.get("version")
            try:
                if not isinstance(version, str):
                    raise ValueError()
                exact_version(version)
                if (
                    type(row.get("installed")) is not bool
                    or type(row.get("broken", False)) is not bool
                ):
                    raise ValueError()
            except (ValueError, TypeError) as exc:
                raise AdapterError(
                    ErrorCode.VERIFICATION_FAILED,
                    "mise inventory did not provide a supported version/status record.",
                ) from exc
            if row["installed"] and not row.get("broken", False):
                versions.add(version)
        return tuple(sorted(versions))


class Uv(RuntimeManager):
    id = "uv"
    runtimes = ("python",)

    def install(self, request: RuntimeRequest) -> Command:
        self.validate(request)
        if any(
            os.environ.get(name)
            for name in (
                "UV_INSECURE_HOST",
                "UV_PYTHON_INSTALL_MIRROR",
                "UV_PYTHON_DOWNLOADS_JSON_URL",
            )
        ):
            raise AdapterError(
                ErrorCode.UNSUPPORTED_OPERATION,
                "Remove uv TLS/mirror overrides before reviewing a runtime download.",
            )
        return self._command(
            (
                "--no-config",
                "--no-progress",
                "python",
                "install",
                "--no-bin",
                "--",
                request.version,
            ),
            mutates=True,
        )

    def inventory(self, request: RuntimeRequest) -> Command:
        self.validate(request)
        return self._command(
            (
                "--no-config",
                "--no-progress",
                "--offline",
                "python",
                "list",
                "--only-installed",
                "--managed-python",
                "--output-format",
                "json",
            )
        )

    def parse_inventory(self, output: str) -> tuple[str, ...]:
        versions = set()
        arch = "aarch64" if self.host.architecture == Architecture.ARM64 else "x86_64"
        for row in self._rows(output):
            key = row.get("key")
            if (
                not isinstance(key, str)
                or not isinstance(row.get("path"), str)
                or not row["path"]
            ):
                raise AdapterError(
                    ErrorCode.VERIFICATION_FAILED,
                    "uv inventory did not identify an installed managed interpreter.",
                )
            match = re.fullmatch(
                r"cpython-([0-9]+\.[0-9]+\.[0-9]+)-"
                + re.escape(self.host.os.value)
                + "-"
                + arch
                + r"-[A-Za-z0-9_]+",
                key,
            )
            if match:
                versions.add(match[1])
        return tuple(sorted(versions))


class Rustup(RuntimeManager):
    id = "rustup"
    runtimes = ("rust",)

    def _triple(self) -> str:
        arch = "aarch64" if self.host.architecture == Architecture.ARM64 else "x86_64"
        suffix = {
            OperatingSystem.WINDOWS: "pc-windows-msvc",
            OperatingSystem.MACOS: "apple-darwin",
            OperatingSystem.LINUX: "unknown-linux-gnu",
        }[self.host.os]
        if (
            self.host.distribution
            and self.host.distribution.family == LinuxFamily.ALPINE
        ):
            suffix = "unknown-linux-musl"
        return f"{arch}-{suffix}"

    def install(self, request: RuntimeRequest) -> Command:
        self.validate(request)
        if any(
            os.environ.get(name)
            for name in ("RUSTUP_DIST_SERVER", "RUSTUP_UPDATE_ROOT")
        ):
            raise AdapterError(
                ErrorCode.UNSUPPORTED_OPERATION,
                "Custom rustup distribution origins require a separately reviewed adapter.",
            )
        return self._command(
            (
                "toolchain",
                "install",
                f"{request.version}-{self._triple()}",
                "--profile",
                "minimal",
                "--no-self-update",
            ),
            mutates=True,
        )

    def inventory(self, request: RuntimeRequest) -> Command:
        self.validate(request)
        return self._command(("toolchain", "list"))

    def parse_inventory(self, output: str) -> tuple[str, ...]:
        versions = set()
        for line in output.splitlines():
            if not line.strip() or line == "no installed toolchains":
                continue
            match = re.fullmatch(
                r"([0-9]+\.[0-9]+\.[0-9]+)-"
                + re.escape(self._triple())
                + r"(?: \([a-z, ]+\))?",
                line,
            )
            if match:
                versions.add(match[1])
            elif not re.fullmatch(r"[A-Za-z0-9_.-]+(?: \([a-z, ]+\))?", line):
                raise AdapterError(
                    ErrorCode.VERIFICATION_FAILED,
                    "rustup inventory was malformed or unsupported.",
                )
        return tuple(sorted(versions))


def default_adapters(host: Platform) -> tuple[RuntimeManager, ...]:
    return Mise(host), Uv(host), Rustup(host)
