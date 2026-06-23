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


class NodeInstaller:

    @staticmethod
    def setup():
        NodeInstaller._install_node()

    @staticmethod
    def _is_node_installed():
        try:
            subprocess.run(
                ["node", "--version"],
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
                    "OpenJS.NodeJS.LTS",
                    "-e",
                    "--accept-source-agreements",
                    "--accept-package-agreements"
                ],
                check=True
            )
        elif OS == "Linux":
            packages = ["nodejs", "npm"]
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
    def _install_node():
        if NodeInstaller._is_node_installed():
            print("\n✓ Node.js is already installed")
            return

        print("Installing Node.js...")
        get_logger().info("Installing Node.js")

        if OS == "Windows" and not winget_available():
            report_failure("Failed to install Node.js", PACKAGE_MANAGER_MISSING_HINT)
            get_logger().error("Failed to install Node.js\n" + PACKAGE_MANAGER_MISSING_HINT)
            return

        try:
            NodeInstaller.download()

        except (subprocess.CalledProcessError, FileNotFoundError, PermissionError) as error:
            if OS == "Windows":
                report_failure("Failed to install Node.js", describe_winget_error(error))
                get_logger().error(f"Failed to install Node.js\n{describe_winget_error(error)}")
            else:
                failure_message = f"Package manager error: {error}"
                report_failure("Failed to install Node.js", failure_message)
                get_logger().error(f"Failed to install Node.js\n{failure_message}")
            return

        print("\n✓ Node.js installed successfully")
        get_logger().info("Node.js installed successfully")


def is_node_installed():
    return NodeInstaller._is_node_installed()


def install_node():
    NodeInstaller._install_node()
