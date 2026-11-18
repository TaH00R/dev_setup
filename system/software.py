"""Read Windows registered applications without running installers or commands."""

import sys
from threading import Event

from models.errors import ErrorCode
from models.inventory import InstalledPackage, Inventory
from package_managers.base import AdapterError


def _clean(value: object) -> str:
    return " ".join(
        "".join(
            c if ord(c) >= 32 and ord(c) != 127 else " " for c in str(value)
        ).split()
    )


def windows_applications(cancel: Event | None = None) -> Inventory:
    if cancel and cancel.is_set():
        raise AdapterError(ErrorCode.CANCELLED, "Application inventory was cancelled.")
    if sys.platform != "win32":
        raise AdapterError(
            ErrorCode.UNSUPPORTED_PLATFORM,
            "Windows application registration is unavailable on this OS.",
        )
    import winreg

    packages = []
    seen_user: set[str] = set()
    root_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    for hive_name, hive in (
        ("machine", winreg.HKEY_LOCAL_MACHINE),
        ("user", winreg.HKEY_CURRENT_USER),
    ):
        for view_name, view in (
            ("64", winreg.KEY_WOW64_64KEY),
            ("32", winreg.KEY_WOW64_32KEY),
        ):
            try:
                root = winreg.OpenKey(hive, root_path, 0, winreg.KEY_READ | view)
            except FileNotFoundError:
                continue
            except OSError as error:
                raise AdapterError(
                    ErrorCode.PERMISSION_DENIED,
                    "Windows application registration could not be read.",
                ) from error
            with root:
                try:
                    count = winreg.QueryInfoKey(root)[0]
                    for index in range(count):
                        if cancel and cancel.is_set():
                            raise AdapterError(
                                ErrorCode.CANCELLED,
                                "Application inventory was cancelled.",
                            )
                        name = winreg.EnumKey(root, index)
                        with winreg.OpenKey(root, name) as key:
                            try:
                                display, _ = winreg.QueryValueEx(key, "DisplayName")
                            except FileNotFoundError:
                                continue
                            try:
                                version, _ = winreg.QueryValueEx(key, "DisplayVersion")
                            except FileNotFoundError:
                                version = ""
                            if not isinstance(display, str) or not display.strip():
                                continue
                            if hive_name == "user" and name in seen_user:
                                continue
                            if hive_name == "user":
                                seen_user.add(name)
                            identity = f"{_clean(display)} [{hive_name}/{view_name}/{_clean(name)}]"
                            packages.append(
                                InstalledPackage(
                                    identity,
                                    (_clean(version),) if _clean(version) else (),
                                )
                            )
                except OSError as error:
                    raise AdapterError(
                        ErrorCode.VERIFICATION_FAILED,
                        "Windows application registration changed or could not be read completely; refresh again.",
                    ) from error
    return Inventory("windows-registry", tuple(packages))
