"""Explicit formula/cask commands; no tap bootstrap or automatic global updates."""

import re

from models.errors import ErrorCode
from models.platform import OperatingSystem
from models.specs import InstallSpec, PrivilegeRequirement
from system.commands import Command

from .base import AdapterError, BackendAction, PackageManager

_ENV = (
    ("HOMEBREW_NO_AUTO_UPDATE", "1"),
    ("HOMEBREW_NO_INSTALL_CLEANUP", "1"),
    ("HOMEBREW_NO_ANALYTICS", "1"),
)


class Homebrew(PackageManager):
    id = "homebrew"
    os = OperatingSystem.MACOS
    executables = ("brew",)

    def validate_spec(self, action: BackendAction, spec: InstallSpec) -> None:
        super().validate_spec(action, spec)
        sources = {
            "homebrew-core": "https://github.com/Homebrew/homebrew-core",
            "homebrew-cask": "https://github.com/Homebrew/homebrew-cask",
        }
        if sources.get(spec.source.id) != spec.source.url:
            raise AdapterError(
                ErrorCode.UNSUPPORTED_OPERATION,
                "Select the reviewed Homebrew core or cask source explicitly.",
            )
        if (
            re.fullmatch(
                r"[a-z0-9][a-z0-9+._-]*(?:@[0-9][a-z0-9._-]*)?", spec.native_id
            )
            is None
        ):
            raise AdapterError(
                ErrorCode.UNSUPPORTED_OPERATION,
                "Homebrew requires an exact token, never a path, tap or URL.",
            )

    def _build(
        self,
        action: BackendAction,
        paths: dict[str, str],
        spec: InstallSpec | None,
        query: str | None,
    ) -> Command:
        prefix = (paths["brew"],)
        args: tuple[str, ...]
        if action == BackendAction.VERSION:
            args = ("--version",)
        elif action == BackendAction.SEARCH:
            assert query is not None
            args = ("search", "--", query)
        elif action == BackendAction.LIST:
            args = ("list", "--versions")
        else:
            assert spec is not None
            kind = "--cask" if spec.source.id == "homebrew-cask" else "--formula"
            native_action = {
                BackendAction.SHOW: "info",
                BackendAction.UPGRADE: "upgrade",
            }.get(action, action.value)
            args = (native_action, kind)
            if action == BackendAction.SHOW:
                args += ("--json=v2",)
            args += ("--", spec.native_id)
        privilege = (
            spec.privilege
            if spec is not None
            and action
            in {BackendAction.INSTALL, BackendAction.UNINSTALL, BackendAction.UPGRADE}
            else PrivilegeRequirement.NONE
        )
        return self.request(
            prefix + args, action, spec, privilege=privilege, environment=_ENV
        )
