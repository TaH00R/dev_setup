"""Normalize explicit facts or inspect the host without running commands."""

import platform
from collections.abc import Mapping

from dev_setup.models.platform import (
    Architecture,
    LinuxDistribution,
    LinuxFamily,
    OperatingSystem,
    Platform,
)


def normalize_os(value: str) -> OperatingSystem:
    return {
        "windows": OperatingSystem.WINDOWS,
        "darwin": OperatingSystem.MACOS,
        "macos": OperatingSystem.MACOS,
        "linux": OperatingSystem.LINUX,
    }.get(value.strip().lower(), OperatingSystem.UNKNOWN)


def normalize_architecture(value: str) -> Architecture:
    return {
        "amd64": Architecture.X86_64,
        "x86_64": Architecture.X86_64,
        "x64": Architecture.X86_64,
        "aarch64": Architecture.ARM64,
        "arm64": Architecture.ARM64,
        "x86": Architecture.X86,
        "i386": Architecture.X86,
        "i486": Architecture.X86,
        "i586": Architecture.X86,
        "i686": Architecture.X86,
        "armv7": Architecture.ARMV7,
        "armv7l": Architecture.ARMV7,
    }.get(value.strip().lower(), Architecture.UNKNOWN)


_FAMILIES = {
    **dict.fromkeys(("debian", "ubuntu", "linuxmint", "pop"), LinuxFamily.DEBIAN),
    **dict.fromkeys(("arch", "manjaro", "endeavouros"), LinuxFamily.ARCH),
    **dict.fromkeys(
        ("fedora", "rhel", "centos", "rocky", "almalinux", "ol"), LinuxFamily.FEDORA
    ),
    **dict.fromkeys(
        ("suse", "opensuse", "opensuse-leap", "opensuse-tumbleweed", "sles", "sled"),
        LinuxFamily.SUSE,
    ),
    "alpine": LinuxFamily.ALPINE,
}


def distribution_from_release(data: Mapping[str, str]) -> LinuxDistribution | None:
    """Use os-release ID first, then ordered ID_LIKE ancestry, without guessing IDs."""
    distro_id = data.get("ID", "").strip().lower()
    if not distro_id:
        return None
    like = tuple(dict.fromkeys(data.get("ID_LIKE", "").strip().lower().split()))
    family = next(
        (_FAMILIES[item] for item in (distro_id, *like) if item in _FAMILIES),
        LinuxFamily.UNKNOWN,
    )
    try:
        return LinuxDistribution(
            id=distro_id,
            family=family,
            like=like,
            version=data.get("VERSION_ID", "").strip() or None,
            name=data.get("PRETTY_NAME", "").strip() or None,
        )
    except ValueError:
        # Malformed host metadata leaves Linux unidentified, not falsely supported.
        return None


def detect_platform(
    *,
    system: str | None = None,
    machine: str | None = None,
    os_release: Mapping[str, str] | None = None,
) -> Platform:
    """Inspect only when called; explicit facts allow deterministic/off-host use."""
    os = normalize_os(platform.system() if system is None else system)
    architecture = normalize_architecture(
        platform.machine() if machine is None else machine
    )
    distribution = None
    if os == OperatingSystem.LINUX:
        if os_release is None:
            try:
                os_release = platform.freedesktop_os_release()
            except OSError:
                os_release = {}
        distribution = distribution_from_release(os_release)
    return Platform(os, architecture, distribution)
