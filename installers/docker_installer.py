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


class DockerInstaller:

    @staticmethod
    def setup():
        DockerInstaller._install_docker()

    @staticmethod
    def _is_docker_installed():
        try:
            subprocess.run(
                ["docker", "--version"],
                capture_output=True,
                text=True,
                check=True,
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
                    "Docker.DockerDesktop",
                    "-e",
                    "--accept-source-agreements",
                    "--accept-package-agreements",
                ],
                check=True,
            )

        elif OS == "Linux":
            match dist:
                case "debian":
                    subprocess.run(["sudo", "apt", "update"], check=True)
                    subprocess.run(
                        ["sudo", "apt", "install", "-y", "docker.io"],
                        check=True,
                    )

                case "arch":
                    subprocess.run(
                        ["sudo", "pacman", "-Syu", "--noconfirm"],
                        check=True,
                    )
                    subprocess.run(
                        ["sudo", "pacman", "-S", "--noconfirm", "docker"],
                        check=True,
                    )

                case "fedora":
                    subprocess.run(
                        ["sudo", "dnf", "upgrade", "--refresh", "-y"],
                        check=True,
                    )
                    subprocess.run(
                        ["sudo", "dnf", "install", "-y", "docker"],
                        check=True,
                    )

                case "opensuse":
                    subprocess.run(
                        ["sudo", "zypper", "refresh"],
                        check=True,
                    )
                    subprocess.run(
                        ["sudo", "zypper", "install", "-y", "docker"],
                        check=True,
                    )

                case _:
                    print("Unsupported distro.")
                    return

    @staticmethod
    def _install_docker():
        if DockerInstaller._is_docker_installed():
            print("\n✓ Docker is already installed")
            return

        print("Installing Docker...")
        get_logger().info("Installing Docker")

        if OS == "Windows" and not winget_available():
            report_failure(
                "Failed to install Docker",
                PACKAGE_MANAGER_MISSING_HINT,
            )
            get_logger().error(
                "Failed to install Docker\n"
                + PACKAGE_MANAGER_MISSING_HINT
            )
            return

        try:
            DockerInstaller.download()

        except (
            subprocess.CalledProcessError,
            FileNotFoundError,
            PermissionError,
        ) as error:

            if OS == "Windows":
                report_failure(
                    "Failed to install Docker",
                    describe_winget_error(error),
                )
                get_logger().error(
                    f"Failed to install Docker\n{describe_winget_error(error)}"
                )
            else:
                failure_message = f"Package manager error: {error}"
                report_failure(
                    "Failed to install Docker",
                    failure_message,
                )
                get_logger().error(
                    f"Failed to install Docker\n{failure_message}"
                )
            return

        if DockerInstaller._is_docker_installed():
            print("\n✓ Docker installed successfully")
            get_logger().info("Docker installed successfully")
        else:
            report_failure(
                "Docker installation completed",
                "Docker command not detected. You may need to restart your terminal or log in again.",
            )


def is_docker_installed():
    return DockerInstaller._is_docker_installed()


def install_docker():
    DockerInstaller._install_docker()