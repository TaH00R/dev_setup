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

class GitInstaller:

    @staticmethod
    def setup():
        install_git()

    @staticmethod
    def _is_git_installed():
        try:
            subprocess.run(
                ["git", "--version"],
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
            subprocess.run(["winget", "install", "Git.Git"], check=True)
        elif OS == "Linux":
            packages = ["git"]
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
    def _install_git():
        if is_git_installed():
            print("\n✓ Git is already installed")
            return

        print("Installing Git...")
        get_logger().info("Installing Git")

        if OS == "Windows" and not winget_available():
            report_failure("Failed to install Git", PACKAGE_MANAGER_MISSING_HINT)
            get_logger().error("Failed to install Git\n" + PACKAGE_MANAGER_MISSING_HINT)
            return

        try:
            GitInstaller.download()

        except (subprocess.CalledProcessError, FileNotFoundError, PermissionError) as error:
            if OS == "Windows":
                report_failure("Failed to install Git", describe_winget_error(error))
                get_logger().error(f"Failed to install Git\n{describe_winget_error(error)}")
            else:
                failure_message = f"Package manager error: {error}"
                report_failure("Failed to install Git", failure_message)
                get_logger().error(f"Failed to install Git\n{failure_message}")
            return

        print("\n✓ Git installed successfully")
        get_logger().info("Git installed successfully")

def is_git_installed():
    return GitInstaller._is_git_installed()


def install_git():
    GitInstaller._install_git()