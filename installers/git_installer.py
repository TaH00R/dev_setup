import subprocess

from installers.errors import (
    WINGET_MISSING_HINT,
    describe_winget_error,
    report_failure,
    winget_available,
)


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
    def _install_git():
        if is_git_installed():
            print("✓ Git is already installed")
            return

        print("Installing Git...")

        if not winget_available():
            report_failure("Failed to install Git", WINGET_MISSING_HINT)
            return

        try:
            subprocess.run(
                [
                    "winget",
                    "install",
                    "-e",
                    "--id",
                    "Git.Git"
                ],
                check=True
            )

            print("✓ Git installed successfully")

        except (subprocess.CalledProcessError, FileNotFoundError, PermissionError) as error:
            report_failure("Failed to install Git", describe_winget_error(error))


def is_git_installed():
    return GitInstaller._is_git_installed()


def install_git():
    GitInstaller._install_git()