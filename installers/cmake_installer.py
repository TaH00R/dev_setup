import subprocess

from installers.errors import (
    WINGET_MISSING_HINT,
    describe_winget_error,
    report_failure,
    winget_available,
)


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
    def _install_cmake():
        if CMakeInstaller._is_cmake_installed():
            print("✓ CMake is already installed")
            return

        print("Installing CMake...")

        if not winget_available():
            report_failure("Failed to install CMake", WINGET_MISSING_HINT)
            return

        try:
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

            print("✓ CMake installed successfully")

        except (subprocess.CalledProcessError, FileNotFoundError, PermissionError) as error:
            report_failure("Failed to install CMake", describe_winget_error(error))
