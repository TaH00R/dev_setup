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


class PythonInstaller:

    @staticmethod
    def setup():
        install_python()

    @staticmethod
    def _is_python_installed():
        try:
            subprocess.run(
                ["python", "--version"],
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
            subprocess.run(["winget", "install", "-e", "--id", "Python.Python.3.13"], check=True)
        elif OS == "Linux":
            packages = ["python3"]
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
    def _install_python():
        if is_python_installed():
            print("\n✓ Python is already installed")
            return

        print("Installing Python...")
        get_logger().info("Installing Python")

        if OS == "Windows" and not winget_available():
            report_failure("Failed to install Python", PACKAGE_MANAGER_MISSING_HINT)
            get_logger().error("Failed to install Python\n" + PACKAGE_MANAGER_MISSING_HINT)
            return

        try:
            PythonInstaller.download()

        except (subprocess.CalledProcessError, FileNotFoundError, PermissionError) as error:
            if OS == "Windows":
                report_failure("Failed to install Python", describe_winget_error(error))
                get_logger().error(f"Failed to install Python\n{describe_winget_error(error)}")
            else:
                failure_message = f"Package manager error: {error}"
                report_failure("Failed to install Python", failure_message)
                get_logger().error(f"Failed to install Python\n{failure_message}")
            return

        print("\n✓ Python installed successfully")
        get_logger().info("Python installed successfully")


def is_python_installed():
    return PythonInstaller._is_python_installed()


def install_python():
    PythonInstaller._install_python()