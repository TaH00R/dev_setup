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

class VSCodeInstaller:

    @staticmethod
    def setup():
        VSCodeInstaller._install_vscode()

    @staticmethod
    def _is_vscode_installed():
        try:
            subprocess.run(
                ["code", "--version"],
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
            subprocess.run(["winget", "install", "Microsoft.VisualStudioCode"], check=True)
        elif OS == "Linux":
            packages = ["code"]
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
    def _install_vscode():
        if VSCodeInstaller._is_vscode_installed():
            print("\n✓ VSCode is already installed")
            return

        print("Installing VSCode...")
        get_logger().info("Installing VSCode")

        if OS == "Windows" and not winget_available():
            report_failure("Failed to install VSCode", PACKAGE_MANAGER_MISSING_HINT)
            get_logger().error("Failed to install VSCode\n" + PACKAGE_MANAGER_MISSING_HINT)
            return

        try:
            VSCodeInstaller.download()

        except (subprocess.CalledProcessError, FileNotFoundError, PermissionError) as error:
            if OS == "Windows":
                report_failure("Failed to install VSCode", describe_winget_error(error))
                get_logger().error(f"Failed to install VSCode\n{describe_winget_error(error)}")
            else:
                failure_message = f"Package manager error: {error}"
                report_failure("Failed to install VSCode", failure_message)
                get_logger().error(f"Failed to install VSCode\n{failure_message}")
            return

        print("\n✓ VSCode installed successfully")
        get_logger().info("VSCode installed successfully")

def is_vscode_installed():
    return VSCodeInstaller._is_vscode_installed()


def install_vscode():
    VSCodeInstaller._install_vscode()
