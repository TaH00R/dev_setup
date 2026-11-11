"""Consistent command-building contract; execution is explicit and consent-bound."""

import re
import shutil
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from models.errors import ErrorCode, OperationError
from models.platform import OperatingSystem, Platform
from models.specs import InstallSpec, PrivilegeRequirement
from system.commands import Approval, Command, CommandResult
from system.runner import CommandRunner, OutputObserver


class BackendAction(str, Enum):
    VERSION = "version"
    SEARCH = "search"
    INSTALL = "install"
    UNINSTALL = "uninstall"
    UPGRADE = "upgrade"
    LIST = "list"
    SHOW = "show"


@dataclass(frozen=True)
class Capabilities:
    actions: frozenset[BackendAction]
    version_selection: frozenset[BackendAction] = frozenset()
    channel_selection: bool = False


@dataclass(frozen=True)
class Availability:
    backend: str
    available: bool
    executables: tuple[tuple[str, str], ...] = ()
    reason: str | None = None


class AdapterError(ValueError):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.error = OperationError(code, message)


Finder = Callable[[str], str | None]


class PackageManager(ABC):
    id: str
    os: OperatingSystem
    executables: tuple[str, ...]
    capabilities = Capabilities(frozenset(BackendAction))

    def __init__(self, host: Platform, *, find: Finder | None = None) -> None:
        self.host = host
        self.find = find or shutil.which

    def supports_host(self) -> bool:
        return self.host.os == self.os and self.host.unsupported_reason is None

    def availability(self) -> Availability:
        if not self.supports_host():
            return Availability(
                self.id,
                False,
                reason="Backend is incompatible with the target platform.",
            )
        found = []
        for name in self.executables:
            path = self.find(name)
            if path is None or not Path(path).is_absolute():
                return Availability(
                    self.id, False, reason="A required native executable is missing."
                )
            found.append((name, path))
        return Availability(self.id, True, tuple(found))

    @staticmethod
    def validate_query(query: str | None) -> str:
        if (
            not isinstance(query, str)
            or not 1 <= len(query) <= 100
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._+@-]*", query) is None
        ):
            raise AdapterError(
                ErrorCode.UNSUPPORTED_OPERATION,
                "Search requires a plain query of at most 100 characters, beginning with a letter or digit.",
            )
        return query

    def validate_spec(self, action: BackendAction, spec: InstallSpec) -> None:
        if spec.backend != self.id or not spec.support.supports(self.host):
            raise AdapterError(
                ErrorCode.UNSUPPORTED_PLATFORM,
                "Selected mapping does not match this backend and target platform.",
            )
        if spec.version is not None:
            if action not in self.capabilities.version_selection:
                raise AdapterError(
                    ErrorCode.UNSUPPORTED_OPERATION,
                    "This action does not support explicit version selection.",
                )
            if (
                spec.version.channel is not None
                and not self.capabilities.channel_selection
            ):
                raise AdapterError(
                    ErrorCode.UNSUPPORTED_OPERATION,
                    "This backend does not support channel selection.",
                )
            if (
                re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+:~_-]*", spec.version.value)
                is None
            ):
                raise AdapterError(
                    ErrorCode.UNSUPPORTED_OPERATION,
                    "Version contains unsupported option or expression syntax.",
                )

    def build(
        self,
        action: BackendAction,
        *,
        spec: InstallSpec | None = None,
        query: str | None = None,
    ) -> Command:
        if (
            not isinstance(action, BackendAction)
            or action not in self.capabilities.actions
        ):
            raise AdapterError(
                ErrorCode.UNSUPPORTED_OPERATION, "Backend capability is not supported."
            )
        if action in {
            BackendAction.INSTALL,
            BackendAction.UNINSTALL,
            BackendAction.UPGRADE,
            BackendAction.SHOW,
        }:
            if not isinstance(spec, InstallSpec):
                raise AdapterError(
                    ErrorCode.UNSUPPORTED_OPERATION,
                    "An exact source-specific InstallSpec is required.",
                )
            self.validate_spec(action, spec)
        elif spec is not None:
            raise AdapterError(
                ErrorCode.UNSUPPORTED_OPERATION,
                "This action does not accept a package specification.",
            )
        if action == BackendAction.SEARCH:
            query = self.validate_query(query)
        elif query is not None:
            raise AdapterError(
                ErrorCode.UNSUPPORTED_OPERATION, "Only search accepts a query."
            )
        available = self.availability()
        if not available.available:
            code = (
                ErrorCode.MISSING_BACKEND
                if self.supports_host()
                else ErrorCode.UNSUPPORTED_PLATFORM
            )
            raise AdapterError(code, available.reason or "Backend is unavailable.")
        return self._build(action, dict(available.executables), spec, query)

    @abstractmethod
    def _build(
        self,
        action: BackendAction,
        paths: dict[str, str],
        spec: InstallSpec | None,
        query: str | None,
    ) -> Command:
        raise NotImplementedError

    def request(
        self,
        argv: tuple[str, ...],
        action: BackendAction,
        spec: InstallSpec | None = None,
        *,
        source_id: str | None = None,
        privilege: PrivilegeRequirement = PrivilegeRequirement.NONE,
        environment: tuple[tuple[str, str], ...] = (),
    ) -> Command:
        mutates = action in {
            BackendAction.INSTALL,
            BackendAction.UNINSTALL,
            BackendAction.UPGRADE,
        }
        return Command(
            argv,
            f"{self.id}: {action.value}",
            self.os,
            source_id or (spec.source.id if spec else self.id),
            mutates,
            privilege,
            1800.0 if mutates else 30.0,
            environment,
        )

    def version(self) -> Command:
        return self.build(BackendAction.VERSION)

    def search(self, query: str) -> Command:
        return self.build(BackendAction.SEARCH, query=query)

    def install(self, spec: InstallSpec) -> Command:
        return self.build(BackendAction.INSTALL, spec=spec)

    def uninstall(self, spec: InstallSpec) -> Command:
        return self.build(BackendAction.UNINSTALL, spec=spec)

    def upgrade(self, spec: InstallSpec) -> Command:
        return self.build(BackendAction.UPGRADE, spec=spec)

    def list_installed(self) -> Command:
        return self.build(BackendAction.LIST)

    def show(self, spec: InstallSpec) -> Command:
        return self.build(BackendAction.SHOW, spec=spec)

    def execute(
        self,
        action: BackendAction,
        runner: CommandRunner,
        *,
        spec: InstallSpec | None = None,
        query: str | None = None,
        approval: Approval | None = None,
        cancel: threading.Event | None = None,
        observe: OutputObserver | None = None,
    ) -> CommandResult:
        """Worker-only execution; rebuild the validated request and bind approval exactly."""
        command = self.build(action, spec=spec, query=query)
        return runner.run(command, approval=approval, cancel=cancel, observe=observe)
