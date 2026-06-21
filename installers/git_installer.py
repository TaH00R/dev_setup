import subprocess

from installers.errors import (
    WINGET_MISSING_HINT,
    describe_winget_error,
    report_failure,
    winget_available,
)


def is_git_installed():
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


def install_git():
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
                "--id",
                "Git.Git",
                "-e",
                "--accept-source-agreements",
                "--accept-package-agreements"
            ],
            check=True
        )

        print("✓ Git installed successfully")

    except (subprocess.CalledProcessError, FileNotFoundError, PermissionError) as error:
        report_failure("Failed to install Git", describe_winget_error(error))