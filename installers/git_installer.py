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
    except (subprocess.CalledProcessError, FileNotFoundError):
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

    except FileNotFoundError:
        print("✗ Failed to install Git.")
        print("  Please verify that Winget is installed and available in PATH.")
        print("  Download it from: https://aka.ms/getwinget")

    except PermissionError:
        print("✗ Failed to install Git.")
        print("  Permission denied. Please run the terminal as Administrator.")

    except subprocess.CalledProcessError as e:
        print(f"✗ Failed to install Git (exit code {e.returncode}).")
        print("  Please verify your internet connection and try again.")
        print("  You can also install Git manually from: https://git-scm.com")

    except OSError as e:
        print(f"✗ Failed to install Git.")
        print(f"  System error: {e}")
