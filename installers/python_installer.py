import subprocess

from installers.errors import (
    WINGET_MISSING_HINT,
    describe_winget_error,
    report_failure,
    winget_available,
)


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
    def _install_python():
        if is_python_installed():
            print("✓ Python is already installed")
            return

        print("Installing Python...")

        if not winget_available():
            report_failure("Failed to install Python", WINGET_MISSING_HINT)
            return

        try:
            subprocess.run(
                [
                    "winget",
                    "install",
                    "-e",
                    "--id",
                    "Python.Python.3.13"
                ],
                check=True
            )

            print("✓ Python installed successfully")

        except (subprocess.CalledProcessError, FileNotFoundError, PermissionError) as error:
            report_failure("Failed to install Python", describe_winget_error(error))


def is_python_installed():
    return PythonInstaller._is_python_installed()


def install_python():
    PythonInstaller._install_python()