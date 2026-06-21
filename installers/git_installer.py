import subprocess


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

    except subprocess.CalledProcessError:
        print("✗ Failed to install Git")