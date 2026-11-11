"""APT cache/inventory and exact package mutations, with native Polkit elevation."""

import os
import re

from models.errors import ErrorCode
from models.platform import LinuxFamily, OperatingSystem
from models.specs import InstallSpec, PrivilegeRequirement
from system.commands import Command

from .base import AdapterError, BackendAction, Capabilities, PackageManager


class APT(PackageManager):
    id = "apt"
    os = OperatingSystem.LINUX
    executables = ("apt-get", "apt-cache", "dpkg-query")
    capabilities = Capabilities(
        frozenset(BackendAction),
        frozenset({BackendAction.INSTALL, BackendAction.UPGRADE, BackendAction.SHOW}),
    )

    def supports_host(self) -> bool:
        return (
            super().supports_host()
            and self.host.distribution is not None
            and self.host.distribution.family == LinuxFamily.DEBIAN
        )

    def validate_spec(self, action: BackendAction, spec: InstallSpec) -> None:
        super().validate_spec(action, spec)
        if (
            spec.source.id != "ubuntu-noble"
            or spec.source.url != "https://packages.ubuntu.com/noble/"
            or self.host.distribution is None
            or (self.host.distribution.id, self.host.distribution.version)
            != ("ubuntu", "24.04")
        ):
            raise AdapterError(
                ErrorCode.UNSUPPORTED_OPERATION,
                "Only the reviewed Ubuntu 24.04 mapping is currently supported for package selection.",
            )
        if (
            re.fullmatch(
                r"[a-z0-9][a-z0-9+.-]*(?::(?:amd64|arm64|i386|armhf))?", spec.native_id
            )
            is None
        ):
            raise AdapterError(
                ErrorCode.UNSUPPORTED_OPERATION,
                "APT requires an exact native package identifier without paths, wildcards or expressions.",
            )

    def _build(
        self,
        action: BackendAction,
        paths: dict[str, str],
        spec: InstallSpec | None,
        query: str | None,
    ) -> Command:
        argv: tuple[str, ...]
        if action == BackendAction.VERSION:
            argv = (paths["apt-get"], "--version")
        elif action == BackendAction.SEARCH:
            assert query is not None
            # APT search uses regular expressions; quote query metacharacters as data.
            argv = (
                paths["apt-cache"],
                "search",
                "--names-only",
                "--",
                re.escape(query),
            )
        elif action == BackendAction.LIST:
            argv = (
                paths["dpkg-query"],
                "--show",
                r"--showformat=${binary:Package}\t${Version}\t${db:Status-Status}\n",
            )
        else:
            assert spec is not None
            token = spec.native_id + ("=" + spec.version.value if spec.version else "")
            if action == BackendAction.SHOW:
                argv = (paths["apt-cache"], "show", "--", token)
            else:
                action_args = (
                    ("remove",)
                    if action == BackendAction.UNINSTALL
                    else ("install", "--only-upgrade")
                    if action == BackendAction.UPGRADE
                    else ("install",)
                )
                argv = (
                    (paths["apt-get"], "--yes", "--target-release", "noble")
                    + action_args
                    + ("--", token)
                )
                if not hasattr(os, "geteuid") or os.geteuid() != 0:
                    agent = self.find("pkexec")
                    if agent is None:
                        raise AdapterError(
                            ErrorCode.PERMISSION_DENIED,
                            "APT changes need root or a native graphical Polkit agent; passwords are never collected.",
                        )
                    argv = (agent, "--disable-internal-agent") + argv
        return self.request(
            argv,
            action,
            spec,
            privilege=PrivilegeRequirement.REQUIRED
            if action
            in {BackendAction.INSTALL, BackendAction.UNINSTALL, BackendAction.UPGRADE}
            else PrivilegeRequirement.NONE,
            environment=(("LC_ALL", "C"),),
        )
