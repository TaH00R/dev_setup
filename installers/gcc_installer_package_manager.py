import subprocess
import platform
import distro

from installers.errors import (
    PACKAGE_MANAGER_MISSING_HINT,
    describe_winget_error,
    report_failure,
    winget_available,
)
from installers.logger import get_logger

OS = platform.system()
dist = distro.like()
if dist == "":
    dist = distro.id()
elif "debian" in dist:
    dist = "debian"
elif "arch" in dist:
    dist = "arch"
elif "fedora" in dist:
    dist = "fedora"

class GCCInstaller:

    @staticmethod
    def setup():
        install_gcc()

    @staticmethod
    def _is_gcc_installed():
        try:
            subprocess.run(
                ["gcc", "--version"],
                capture_output=True,
                text=True,
                check=True
            )
            return True
        except Exception:
            return False
    
    @staticmethod
    def download():
        if OS == "Windows":
            subprocess.run(["winget", "install", "BrechtSanders.WinLibs.POSIX.UCRT"], check=True)
            subprocess.run(["winget", "install", "GnuWin32.Make"], check=True)
        elif OS == "Linux":
            packages = ["gcc", "gdb", "make"]
            match dist:
                case "debian":
                    subprocess.run(["sudo", "apt", "update"])
                    subprocess.run(["sudo", "apt", "install", "-y"] + packages)
                case "arch":
                    subprocess.run(["sudo", "pacman", "-Syu"])
                    subprocess.run(["sudo", "pacman", "-S", "--noconfirm"] + packages)
                case "fedora":
                    subprocess.run(["sudo", "dnf", "upgrade", "--refresh"])
                    subprocess.run(["sudo", "dnf", "install", "-y"] + packages)
                case "opensuse":
                    subprocess.run(["sudo", "zypper", "refresh"])
                    subprocess.run(["sudo", "zypper", "install", "-y"] + packages)
                case _:
                    print("Unsupported distro. Why did you choose something so goofy.")
                    return

    @staticmethod
    def _install_gcc():
        if is_gcc_installed():
            print("✓ GCC is already installed")
            return

        print("Installing GCC...")
        get_logger().info("Installing GCC")

        if OS == "Windows" and not winget_available():
            report_failure("Failed to install GCC", PACKAGE_MANAGER_MISSING_HINT)
            get_logger().error("Failed to install GCC\n" + PACKAGE_MANAGER_MISSING_HINT)
            return

        try:
            GCCInstaller.download()
        except (subprocess.CalledProcessError, FileNotFoundError, PermissionError) as error:
            if OS == "Windows":
                report_failure("Failed to install GCC", describe_winget_error(error))
                get_logger().error(f"Failed to install GCC\n{describe_winget_error(error)}")
            else:
                failure_message = f"Package manager error: {error}"
                report_failure("Failed to install GCC", failure_message)
                get_logger().error(f"Failed to install GCC\n{failure_message}")
            return

        print("✓ GCC installed successfully")
        get_logger().info("GCC installed successfully")


def is_gcc_installed():
    return GCCInstaller._is_gcc_installed()


def install_gcc():
    GCCInstaller._install_gcc()