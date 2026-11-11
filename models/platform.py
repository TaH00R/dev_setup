"""Platform identity independent of host discovery."""

from dataclasses import dataclass
from enum import Enum

from ._validation import enum_value, identifier, text, unique_tuple


class OperatingSystem(str, Enum):
    WINDOWS = "windows"
    MACOS = "macos"
    LINUX = "linux"
    UNKNOWN = "unknown"


class Architecture(str, Enum):
    X86_64 = "x86_64"
    ARM64 = "arm64"
    X86 = "x86"
    ARMV7 = "armv7"
    UNKNOWN = "unknown"


class LinuxFamily(str, Enum):
    DEBIAN = "debian"
    ARCH = "arch"
    FEDORA = "fedora"
    SUSE = "suse"
    ALPINE = "alpine"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class LinuxDistribution:
    id: str
    family: LinuxFamily = LinuxFamily.UNKNOWN
    like: tuple[str, ...] = ()
    version: str | None = None
    name: str | None = None

    def __post_init__(self) -> None:
        identifier(self.id, "distribution id")
        enum_value(self.family, LinuxFamily, "distribution family")
        unique_tuple(self.like, "distribution like")
        for item in self.like:
            identifier(item, "distribution like entry")
        for field in ("version", "name"):
            value = getattr(self, field)
            if value is not None:
                text(value, field)


@dataclass(frozen=True)
class Platform:
    os: OperatingSystem
    architecture: Architecture
    distribution: LinuxDistribution | None = None

    def __post_init__(self) -> None:
        enum_value(self.os, OperatingSystem, "os")
        enum_value(self.architecture, Architecture, "architecture")
        if self.distribution is not None:
            if not isinstance(self.distribution, LinuxDistribution):
                raise ValueError("distribution must be a LinuxDistribution")
            if self.os != OperatingSystem.LINUX:
                raise ValueError("only Linux platforms may have a distribution")

    @property
    def unsupported_reason(self) -> str | None:
        if self.os == OperatingSystem.UNKNOWN:
            return "The operating system is not supported."
        if self.architecture == Architecture.UNKNOWN:
            return "The processor architecture is not recognized."
        if self.os == OperatingSystem.LINUX:
            if self.distribution is None:
                return "The Linux distribution could not be identified."
            if self.distribution.family == LinuxFamily.UNKNOWN:
                return (
                    f"Linux distribution '{self.distribution.id}' has no known family."
                )
        return None
