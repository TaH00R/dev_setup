import subprocess
import distro
import platform

from app.legacy.installers.errors import (
    PACKAGE_MANAGER_MISSING_HINT,
    describe_winget_error,
    report_failure,
    winget_available,
)
from app.legacy.installers.logger import get_logger

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


class CMakeInstaller:

    @staticmethod
    def setup():
        CMakeInstaller._install_cmake()

    @staticmethod
    def _is_cmake_installed():
        try:
            subprocess.run(
                ["cmake", "--version"],
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
            subprocess.run(
                [
                    "winget",
                    "install",
                    "--id",
                    "Kitware.CMake",
                    "-e",
                    "--accept-source-agreements",
                    "--accept-package-agreements"
                ],
                check=True
            )
        elif OS == "Linux":
            packages = ["cmake"]
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
    def _install_cmake():
        if CMakeInstaller._is_cmake_installed():
            print("\n✓ CMake is already installed")
            return

        print("Installing CMake...")
        get_logger().info("Installing CMake")

        if OS == "Windows" and not winget_available():
            report_failure("Failed to install CMake", PACKAGE_MANAGER_MISSING_HINT)
            get_logger().error("Failed to install CMake\n" + PACKAGE_MANAGER_MISSING_HINT)
            return

        try:
            CMakeInstaller.download()

        except (subprocess.CalledProcessError, FileNotFoundError, PermissionError) as error:
            if OS == "Windows":
                report_failure("Failed to install CMake", describe_winget_error(error))
                get_logger().error(f"Failed to install CMake\n{describe_winget_error(error)}")
            else:
                failure_message = f"Package manager error: {error}"
                report_failure("Failed to install CMake", failure_message)
                get_logger().error(f"Failed to install CMake\n{failure_message}")
            return

        print("\n✓ CMake installed successfully")
        get_logger().info("CMake installed successfully")


def is_cmake_installed():
    return CMakeInstaller._is_cmake_installed()


def install_cmake():
    CMakeInstaller._install_cmake()
