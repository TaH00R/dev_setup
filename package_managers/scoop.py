"""Scoop through the explicitly authorized, fixed -File bridge."""

import os
import re
import shutil
from dataclasses import replace
from pathlib import Path

from models.errors import ErrorCode
from models.platform import OperatingSystem, Platform
from models.specs import InstallSpec, PrivilegeRequirement
from system.commands import Command

from .base import AdapterError, Availability, BackendAction, Finder, PackageManager


def _find_scoop_tool(name: str) -> str | None:
    if name != "scoop.ps1":
        return shutil.which(name)
    # Windows PATHEXT commonly omits .ps1; do not use a batch shim or search CWD.
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        root = Path(directory)
        if not root.is_absolute():
            continue
        candidate = root / name
        if candidate.is_file():
            return str(candidate)
    return None


class Scoop(PackageManager):
    id = "scoop"
    os = OperatingSystem.WINDOWS
    executables = ()

    def __init__(self, host: Platform, *, find: Finder | None = None) -> None:
        super().__init__(host, find=find or _find_scoop_tool)

    def availability(self) -> Availability:
        if not self.supports_host():
            return Availability(
                self.id, False, reason="Scoop is supported only on Windows."
            )
        powershell = self.find("pwsh.exe") or self.find("powershell.exe")
        scoop = self.find("scoop.ps1")
        if powershell is None or scoop is None:
            return Availability(
                self.id,
                False,
                reason="A native PowerShell executable and the installed Scoop script are required.",
            )
        script = Path(scoop)
        if (
            not script.is_absolute()
            or script.name.lower() != "scoop.ps1"
            or not script.is_file()
        ):
            return Availability(
                self.id,
                False,
                reason="The installed Scoop script must be an explicit local scoop.ps1 file.",
            )
        if (
            Path(powershell).suffix.lower() != ".exe"
            or not Path(powershell).is_absolute()
        ):
            return Availability(
                self.id,
                False,
                reason="PowerShell must resolve to a native executable, not a command shim.",
            )
        return Availability(
            self.id,
            True,
            (("powershell", powershell), ("scoop", str(script.resolve()))),
        )

    def validate_spec(self, action: BackendAction, spec: InstallSpec) -> None:
        super().validate_spec(action, spec)
        sources = {
            "scoop-main": ("main", "https://github.com/ScoopInstaller/Main"),
            "scoop-extras": ("extras", "https://github.com/ScoopInstaller/Extras"),
        }
        selected = sources.get(spec.source.id)
        if selected is None or spec.source.url != selected[1]:
            raise AdapterError(
                ErrorCode.UNSUPPORTED_OPERATION,
                "Select an explicitly reviewed Scoop main or extras bucket.",
            )
        if (
            re.fullmatch(r"[a-z0-9][a-z0-9._-]*", spec.native_id) is None
            or spec.native_id == "scoop"
        ):
            raise AdapterError(
                ErrorCode.UNSUPPORTED_OPERATION,
                "Scoop requires a plain exact package token without paths, URLs or version expressions.",
            )

    def search_json(self, query: str) -> Command:
        command = self.search(query)
        argv = list(command.argv)
        argv[argv.index("-Action") + 1] = "search-json"
        return replace(command, argv=tuple(argv))

    def _build(
        self,
        action: BackendAction,
        paths: dict[str, str],
        spec: InstallSpec | None,
        query: str | None,
    ) -> Command:
        bridge = str(Path(__file__).with_name("scoop_bridge.ps1").resolve())
        argv: tuple[str, ...] = (
            paths["powershell"],
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            bridge,
            "-ScoopScript",
            paths["scoop"],
            "-Action",
            action.value,
        )
        if action == BackendAction.SEARCH:
            assert query is not None
            argv += ("-Query", query)
        elif spec is not None:
            bucket = "main" if spec.source.id == "scoop-main" else "extras"
            argv += ("-Package", f"{bucket}/{spec.native_id}")
        # No policy bypass, global installation, bucket addition, URL, or -Command.
        privilege = (
            spec.privilege
            if spec is not None
            and action
            in {BackendAction.INSTALL, BackendAction.UNINSTALL, BackendAction.UPGRADE}
            else PrivilegeRequirement.NONE
        )
        command = self.request(argv, action, spec, privilege=privilege)
        if action == BackendAction.UPGRADE:
            command = replace(
                command,
                description="scoop: upgrade; native Scoop may also refresh itself and configured buckets",
            )
        return command
