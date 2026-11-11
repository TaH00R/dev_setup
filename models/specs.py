"""Declarative platform mappings and detection; never executable catalog hooks."""

import re
from dataclasses import dataclass
from datetime import date
from enum import Enum

from ._validation import enum_value, identifier, text, unique_tuple
from .package import PackageSource, PackageVersion, https_url
from .platform import Architecture, OperatingSystem, Platform


class PrivilegeRequirement(str, Enum):
    NONE = "none"
    NATIVE_PROMPT = "native_prompt"
    REQUIRED = "required"


@dataclass(frozen=True)
class PlatformSupport:
    os: OperatingSystem
    architectures: tuple[Architecture, ...]
    distributions: tuple[str, ...] = ()
    distribution_versions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        enum_value(self.os, OperatingSystem, "supported OS")
        if self.os == OperatingSystem.UNKNOWN:
            raise ValueError("unknown OS cannot be advertised as supported")
        unique_tuple(self.architectures, "architectures")
        if not self.architectures:
            raise ValueError("support requires explicit architectures")
        for arch in self.architectures:
            enum_value(arch, Architecture, "supported architecture")
            if arch == Architecture.UNKNOWN:
                raise ValueError(
                    "unknown architecture cannot be advertised as supported"
                )
        unique_tuple(self.distributions, "distributions")
        for distro in self.distributions:
            identifier(distro, "supported distribution")
        if self.os == OperatingSystem.LINUX and not self.distributions:
            raise ValueError("Linux support requires exact reviewed distribution IDs")
        if self.os != OperatingSystem.LINUX and self.distributions:
            raise ValueError("only Linux support can specify distributions")
        unique_tuple(self.distribution_versions, "distribution versions")
        if self.os != OperatingSystem.LINUX and self.distribution_versions:
            raise ValueError("only Linux support can restrict distribution versions")
        for version in self.distribution_versions:
            text(version, "supported distribution version")

    def supports(self, host: Platform) -> bool:
        if host.os != self.os or host.architecture not in self.architectures:
            return False
        return self.os != OperatingSystem.LINUX or (
            host.distribution is not None
            and host.distribution.id in self.distributions
            and (
                not self.distribution_versions
                or host.distribution.version in self.distribution_versions
            )
        )


@dataclass(frozen=True)
class MappingReview:
    """Evidence of source metadata review, not a successful installation test."""

    url: str
    checked_on: date

    def __post_init__(self) -> None:
        https_url(self.url, "mapping review URL")
        if type(self.checked_on) is not date:
            raise ValueError("mapping review date must be a date")


@dataclass(frozen=True)
class DetectionSpec:
    executable: str
    version_args: tuple[str, ...] = ("--version",)
    timeout_seconds: float = 5.0
    support: PlatformSupport | None = None

    def __post_init__(self) -> None:
        text(self.executable, "detection executable")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", self.executable) is None:
            raise ValueError("detection executable must be a bare executable name")
        # Limit metadata to reviewed read-only version flags, never scripts or paths.
        if self.version_args not in (("--version",), ("-version",), ("-V",), ("-v",)):
            # A generic `python version` would execute a file called `version`.
            executable = self.executable.lower().removesuffix(".exe")
            if self.version_args != ("version",) or executable not in {
                "go",
                "docker",
                "podman",
                "helm",
                "kubectl",
                "adb",
            }:
                raise ValueError(
                    "detection requires one safe version flag or a reviewed subcommand"
                )
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not 0 < self.timeout_seconds <= 60
        ):
            raise ValueError("detection timeout must be between 0 and 60 seconds")
        if self.support is not None and not isinstance(self.support, PlatformSupport):
            raise ValueError("detection support must be a PlatformSupport")


@dataclass(frozen=True)
class InstallSpec:
    support: PlatformSupport
    backend: str
    native_id: str
    source: PackageSource
    version: PackageVersion | None = None
    privilege: PrivilegeRequirement = PrivilegeRequirement.NATIVE_PROMPT
    estimated_size_bytes: int | None = None
    restart_required: bool = False
    notes: tuple[str, ...] = ()
    review: MappingReview | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.support, PlatformSupport):
            raise ValueError("install support must be a PlatformSupport")
        identifier(self.backend, "backend")
        text(self.native_id, "native package id")
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+@/:~-]*", self.native_id) is None:
            raise ValueError("native package id contains unsafe identifier syntax")
        if not isinstance(self.source, PackageSource):
            raise ValueError("install source must be a PackageSource")
        if self.version is not None and not isinstance(self.version, PackageVersion):
            raise ValueError("install version must be a PackageVersion")
        enum_value(self.privilege, PrivilegeRequirement, "privilege requirement")
        if self.estimated_size_bytes is not None and (
            type(self.estimated_size_bytes) is not int or self.estimated_size_bytes < 0
        ):
            raise ValueError("estimated size must be a non-negative byte count")
        if type(self.restart_required) is not bool:
            raise ValueError("restart requirement must be a boolean")
        unique_tuple(self.notes, "install notes")
        for note in self.notes:
            text(note, "install note")
        if self.review is not None and not isinstance(self.review, MappingReview):
            raise ValueError("mapping review must be a MappingReview")
