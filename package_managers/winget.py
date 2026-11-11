"""WinGet exact-ID commands restricted to the reviewed community source."""

import re

from models.errors import ErrorCode
from models.platform import OperatingSystem
from models.specs import InstallSpec, PrivilegeRequirement
from system.commands import Command

from .base import AdapterError, BackendAction, Capabilities, PackageManager


class WinGet(PackageManager):
    id = "winget"
    os = OperatingSystem.WINDOWS
    executables = ("winget.exe",)
    capabilities = Capabilities(
        frozenset(BackendAction),
        frozenset({BackendAction.INSTALL, BackendAction.UPGRADE, BackendAction.SHOW}),
    )

    def validate_spec(self, action: BackendAction, spec: InstallSpec) -> None:
        super().validate_spec(action, spec)
        if (
            spec.source.id != "winget"
            or spec.source.url != "https://github.com/microsoft/winget-pkgs"
        ):
            raise AdapterError(
                ErrorCode.UNSUPPORTED_OPERATION,
                "Only the explicitly selected WinGet community source is supported.",
            )
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", spec.native_id) is None:
            raise AdapterError(
                ErrorCode.UNSUPPORTED_OPERATION, "Invalid exact WinGet identifier."
            )

    def _build(
        self,
        action: BackendAction,
        paths: dict[str, str],
        spec: InstallSpec | None,
        query: str | None,
    ) -> Command:
        prefix = (paths["winget.exe"],)
        if action == BackendAction.VERSION:
            return self.request(prefix + ("--version",), action)
        base = prefix + (action.value if action != BackendAction.LIST else "list",)
        args: tuple[str, ...]
        if action == BackendAction.SEARCH:
            assert query is not None
            args = ("--query", query)
        elif action == BackendAction.LIST:
            args = ()
        else:
            assert spec is not None
            args = ("--id", spec.native_id, "--exact")
            if spec.version is not None:
                args += ("--version", spec.version.value)
        # Do not silently accept source/package agreements or bypass elevation.
        args += ("--source", "winget", "--disable-interactivity")
        return self.request(
            base + args,
            action,
            spec,
            privilege=PrivilegeRequirement.NATIVE_PROMPT
            if action
            in {BackendAction.INSTALL, BackendAction.UNINSTALL, BackendAction.UPGRADE}
            else PrivilegeRequirement.NONE,
        )
