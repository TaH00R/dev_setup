"""Select compatible adapters without detecting executables or executing commands."""

from models.platform import OperatingSystem, Platform

from .apt import APT
from .base import Finder, PackageManager
from .homebrew import Homebrew
from .scoop import Scoop
from .winget import WinGet


def adapters_for(
    host: Platform, *, find: Finder | None = None
) -> tuple[PackageManager, ...]:
    candidates: tuple[type[PackageManager], ...] = {
        OperatingSystem.WINDOWS: (WinGet, Scoop),
        OperatingSystem.MACOS: (Homebrew,),
        OperatingSystem.LINUX: (APT,),
    }.get(host.os, ())
    return tuple(
        adapter
        for kind in candidates
        if (adapter := kind(host, find=find)).supports_host()
    )
