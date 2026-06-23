import subprocess
import distro
import platform

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


class JavaInstaller:

    @staticmethod
    def setup():
        JavaInstaller._install_java()

    @staticmethod
    def _is_java_installed():
        try:
            subprocess.run(
                ["java", "-version"],
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
            subprocess.run(["winget", "install", "Microsoft.OpenJDK.21"], check=True)
        elif OS == "Linux":
            match dist:
                case "debian":
                    packages = ["default-jdk"]
                    subprocess.run(["sudo", "apt", "update"])
                    subprocess.run(["sudo", "apt", "install", "-y"] + packages)
                case "arch":
                    packages = ["jdk-openjdk"]
                    subprocess.run(["sudo", "pacman", "-Syu"])
                    subprocess.run(["sudo", "pacman", "-S", "--noconfirm"] + packages)
                case "fedora":
                    packages = ["java-latest-openjdk-devel"]
                    subprocess.run(["sudo", "dnf", "upgrade", "--refresh"])
                    subprocess.run(["sudo", "dnf", "install", "-y"] + packages)
                case "opensuse":
                    packages = ["java-21-openjdk"]
                    subprocess.run(["sudo", "zypper", "refresh"])
                    subprocess.run(["sudo", "zypper", "install", "-y"] + packages)
                case _:
                    print("Unsupported distro. Why did you choose something so goofy.")
                    return
                
    @staticmethod
    def _install_java():
        if JavaInstaller._is_java_installed():
            print("\n✓ Java is already installed")
            return

        print("Installing Java...")
        get_logger().info("Installing Java")

        if OS == "Windows" and not winget_available():
            report_failure("Failed to install Java", PACKAGE_MANAGER_MISSING_HINT)
            return

        try:
            JavaInstaller.download()
        except (subprocess.CalledProcessError, FileNotFoundError, PermissionError) as error:
            if OS == "Windows":
                report_failure("Failed to install Java", describe_winget_error(error))
            else:
                report_failure("Failed to install Java", f"Package manager error: {error}")
            get_logger().error(f"Failed to install Java\n{error}")
            return

        print("\n✓ Java installed successfully")
        get_logger().info("Java installed successfully")