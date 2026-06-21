import subprocess

from installers.errors import (
    WINGET_MISSING_HINT,
    describe_winget_error,
    report_failure,
    winget_available,
)


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
    def _install_node():
        if NodeInstaller._is_node_installed():
            print("✓ Node.js is already installed")
            return

        print("Installing Node.js...")

        if not winget_available():
            report_failure("Failed to install Node.js", WINGET_MISSING_HINT)
            return

        try:
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

            print("✓ Node.js installed successfully")

        except (subprocess.CalledProcessError, FileNotFoundError, PermissionError) as error:
            report_failure("Failed to install Node.js", describe_winget_error(error))
