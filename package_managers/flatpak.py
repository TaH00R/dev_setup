"""Per-user Flathub applications; no remote setup, overrides or system scope."""

import re
import threading
from dataclasses import replace

from models.errors import ErrorCode, OperationError
from models.planning import utc_now
from models.platform import Architecture, OperatingSystem
from models.specs import InstallSpec, PrivilegeRequirement
from system.commands import Approval, Command, CommandResult
from system.runner import CommandRunner, OutputObserver

from .base import AdapterError, PackageManager
from .base import BackendAction as Action


class Flatpak(PackageManager):
    id = "flatpak"
    os = OperatingSystem.LINUX
    executables = ("flatpak",)

    def supports_host(self) -> bool:
        return super().supports_host() and self.host.architecture in {
            Architecture.X86_64,
            Architecture.ARM64,
        }

    def validate_spec(self, action: Action, spec: InstallSpec) -> None:
        super().validate_spec(action, spec)
        if spec.privilege != PrivilegeRequirement.NONE:
            raise AdapterError(
                ErrorCode.UNSUPPORTED_OPERATION,
                "Only the per-user Flatpak scope without elevation is supported.",
            )
        if spec.source.id != "flathub" or spec.source.url != "https://flathub.org":
            raise AdapterError(
                ErrorCode.UNSUPPORTED_OPERATION,
                "An explicitly reviewed Flathub source is required.",
            )
        if (
            re.fullmatch(
                r"[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z][A-Za-z0-9_-]*){2,}",
                spec.native_id,
            )
            is None
        ):
            raise AdapterError(
                ErrorCode.UNSUPPORTED_OPERATION,
                "A full application ID is required; paths and partial IDs are forbidden.",
            )

    def _build(
        self,
        action: Action,
        paths: dict[str, str],
        spec: InstallSpec | None,
        query: str | None,
    ) -> Command:
        args: tuple[str, ...]
        if action == Action.VERSION:
            args = ("--version",)
        elif action == Action.SEARCH:
            assert query is not None
            args = (
                "search",
                "--user",
                "--columns=application,name,description,remotes",
                "--",
                query,
            )
        elif action == Action.LIST:
            args = (
                "list",
                "--user",
                "--app",
                "--columns=application,version,origin,branch,arch",
            )
        else:
            assert spec is not None
            arch = (
                "x86_64" if self.host.architecture == Architecture.X86_64 else "aarch64"
            )
            reference = f"app/{spec.native_id}/{arch}/stable"
            if action == Action.SHOW:
                args = ("remote-info", "--user", "flathub", reference)
            else:
                verb = "update" if action == Action.UPGRADE else action.value
                args = (
                    (verb, "--user", "--noninteractive")
                    + (("flathub",) if action == Action.INSTALL else ())
                    + (reference,)
                )
        command = self.request((paths["flatpak"],) + args, action, spec)
        if action in {Action.INSTALL, Action.UPGRADE}:
            command = replace(
                command,
                description=f"flatpak: {action.value}; native runtimes/extensions and runtime cleanup may also change",
            )
        return command

    def execute(
        self,
        action: Action,
        runner: CommandRunner,
        *,
        spec: InstallSpec | None = None,
        query: str | None = None,
        approval: Approval | None = None,
        cancel: threading.Event | None = None,
        observe: OutputObserver | None = None,
    ) -> CommandResult:
        command = self.build(action, spec=spec, query=query)
        if command.requires_consent and (
            approval is None or not approval.permits(command)
        ):
            return runner.run(
                command, approval=approval, cancel=cancel, observe=observe
            )
        if spec is not None:
            executable = command.argv[0]
            config = self.request(
                (executable, "remotes", "--user", "--columns=name,url"), Action.LIST
            )
            result = runner.run(config, cancel=cancel)
            if not result.succeeded:
                return result
            if result.output_truncated:
                return CommandResult(
                    command.preview(),
                    utc_now(),
                    utc_now(),
                    error=OperationError(
                        ErrorCode.COMMAND_FAILED,
                        "Remote configuration was truncated; source verification was refused.",
                    ),
                )
            selected = [
                line.split()
                for line in result.stdout.splitlines()
                if line.split() and line.split()[0] == "flathub"
            ]
            if (
                len(selected) != 1
                or len(selected[0]) != 2
                or selected[0][1].rstrip("/")
                not in {"https://dl.flathub.org/repo", "https://flathub.org/repo"}
            ):
                return CommandResult(
                    command.preview(),
                    utc_now(),
                    utc_now(),
                    error=OperationError(
                        ErrorCode.PERMISSION_DENIED,
                        "The per-user Flathub remote is absent or has an unreviewed URL; no remote was added.",
                    ),
                )
            if action in {Action.UNINSTALL, Action.UPGRADE}:
                origin = self.request(
                    (executable, "info", "--user", "--show-origin", command.argv[-1]),
                    Action.SHOW,
                    spec,
                )
                result = runner.run(origin, cancel=cancel)
                if not result.succeeded:
                    return result
                if result.output_truncated or result.stdout.strip() != "flathub":
                    return CommandResult(
                        command.preview(),
                        utc_now(),
                        utc_now(),
                        error=OperationError(
                            ErrorCode.PERMISSION_DENIED,
                            "Installed application origin does not match the selected Flathub source.",
                        ),
                    )
        return runner.run(command, approval=approval, cancel=cancel, observe=observe)
