"""Distro-matched native commands with reviewed repository selections."""

import os
import re
from abc import abstractmethod

from models.errors import ErrorCode
from models.platform import LinuxFamily, OperatingSystem
from models.specs import InstallSpec, PrivilegeRequirement
from system.commands import Command

from .base import AdapterError, Capabilities, PackageManager
from .base import BackendAction as Action

_MUTATIONS = {Action.INSTALL, Action.UNINSTALL, Action.UPGRADE}


class LinuxManager(PackageManager):
    os = OperatingSystem.LINUX
    family: LinuxFamily
    source_id: str
    source_url: str
    distro: str
    releases: tuple[str, ...] = ()

    def supports_host(self) -> bool:
        return (
            super().supports_host()
            and self.host.distribution is not None
            and self.host.distribution.family == self.family
        )

    def validate_spec(self, action: Action, spec: InstallSpec) -> None:
        super().validate_spec(action, spec)
        distro = self.host.distribution
        if (
            spec.source.id != self.source_id
            or spec.source.url != self.source_url
            or distro is None
            or distro.id != self.distro
            or (self.releases and distro.version not in self.releases)
        ):
            raise AdapterError(
                ErrorCode.UNSUPPORTED_OPERATION,
                "Selected source or release has no reviewed mapping for this backend.",
            )
        if re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9+._-]*", spec.native_id
        ) is None or spec.native_id.lower().endswith((".rpm", ".apk")):
            raise AdapterError(
                ErrorCode.UNSUPPORTED_OPERATION,
                "An exact package name is required; paths, globs and expressions are forbidden.",
            )

    def _build(
        self,
        action: Action,
        paths: dict[str, str],
        spec: InstallSpec | None,
        query: str | None,
    ) -> Command:
        args = self.arguments(action, spec.native_id if spec else None, query)
        argv = (paths[self.executables[0]],) + args
        if action in _MUTATIONS and (not hasattr(os, "geteuid") or os.geteuid() != 0):
            agent = self.find("pkexec")
            if agent is None or not os.path.isabs(agent):
                raise AdapterError(
                    ErrorCode.PERMISSION_DENIED,
                    "System changes require root or a native Polkit agent; no password input is collected.",
                )
            argv = (agent, "--disable-internal-agent") + argv
        return self.request(
            argv,
            action,
            spec,
            privilege=PrivilegeRequirement.REQUIRED
            if action in _MUTATIONS
            else PrivilegeRequirement.NONE,
            environment=(("LC_ALL", "C"),),
        )

    @abstractmethod
    def arguments(
        self, action: Action, native_id: str | None, query: str | None
    ) -> tuple[str, ...]:
        raise NotImplementedError


class Pacman(LinuxManager):
    id = "pacman"
    executables = ("pacman",)
    family = LinuxFamily.ARCH
    distro = "arch"
    source_id = "arch-extra"
    source_url = "https://archlinux.org/packages/"
    capabilities = Capabilities(frozenset(Action) - {Action.UPGRADE})

    def arguments(
        self, action: Action, native_id: str | None, query: str | None
    ) -> tuple[str, ...]:
        if action == Action.VERSION:
            return ("--version",)
        if action == Action.SEARCH:
            assert query is not None
            return ("-Ss", "--", re.escape(query))
        if action == Action.LIST:
            return ("-Q",)
        assert native_id is not None
        if action == Action.SHOW:
            return ("-Si", "--", "extra/" + native_id)
        if action == Action.UNINSTALL:
            return ("-R", "--noconfirm", "--", native_id)
        return ("-S", "--needed", "--noconfirm", "--", "extra/" + native_id)


class DNF(LinuxManager):
    id = "dnf"
    executables = ("dnf",)
    family = LinuxFamily.FEDORA
    distro = "fedora"
    releases = ("43",)
    source_id = "fedora-43"
    source_url = "https://packages.fedoraproject.org/"

    def arguments(
        self, action: Action, native_id: str | None, query: str | None
    ) -> tuple[str, ...]:
        if action == Action.VERSION:
            return ("--version",)
        if action == Action.SEARCH:
            assert query is not None
            return ("--cacheonly", "search", "--", query)
        if action == Action.LIST:
            return ("--cacheonly", "list", "--installed")
        assert native_id is not None
        repos = ("--repo=fedora", "--repo=updates")
        if action == Action.SHOW:
            return ("--cacheonly",) + repos + ("info", "--", native_id)
        verb = {
            Action.INSTALL: "install",
            Action.UNINSTALL: "remove",
            Action.UPGRADE: "upgrade",
        }[action]
        return (
            ("--assumeyes",)
            + repos
            + ("--setopt=clean_requirements_on_remove=False", verb, "--", native_id)
        )


class Zypper(LinuxManager):
    id = "zypper"
    executables = ("zypper",)
    family = LinuxFamily.SUSE
    distro = "opensuse-tumbleweed"
    source_id = "opensuse-tumbleweed-oss"
    source_url = "https://download.opensuse.org/tumbleweed/repo/oss/"

    def arguments(
        self, action: Action, native_id: str | None, query: str | None
    ) -> tuple[str, ...]:
        prefix = ("--non-interactive", "--no-refresh")
        if action == Action.VERSION:
            return ("--version",)
        if action == Action.SEARCH:
            assert query is not None
            return prefix + ("search", "--type", "package", "--", query)
        if action == Action.LIST:
            return prefix + ("search", "--installed-only", "--type", "package")
        assert native_id is not None
        verb = {
            Action.SHOW: "info",
            Action.INSTALL: "install",
            Action.UNINSTALL: "remove",
            Action.UPGRADE: "update",
        }[action]
        repository = () if action == Action.UNINSTALL else ("--repo", "repo-oss")
        return prefix + (verb, "--type", "package") + repository + ("--", native_id)


class APK(LinuxManager):
    id = "apk"
    executables = ("apk",)
    family = LinuxFamily.ALPINE
    distro = "alpine"
    releases = tuple(f"3.22.{patch}" for patch in range(9))
    source_id = "alpine-3.22-main"
    source_url = "https://dl-cdn.alpinelinux.org/alpine/v3.22/main"
    capabilities = Capabilities(frozenset(Action) - {Action.UPGRADE})

    def arguments(
        self, action: Action, native_id: str | None, query: str | None
    ) -> tuple[str, ...]:
        if action == Action.VERSION:
            return ("--version",)
        if action == Action.SEARCH:
            assert query is not None
            return ("--no-network", "search", "--", query)
        if action == Action.LIST:
            return ("info", "--verbose")
        assert native_id is not None
        repository = (
            "--repositories-file",
            "/dev/null",
            "--repository",
            self.source_url,
        )
        if action == Action.SHOW:
            return ("--no-network",) + repository + ("policy", "--", native_id)
        return (
            repository
            + (("del",) if action == Action.UNINSTALL else ("add",))
            + ("--", native_id)
        )
