import subprocess


def is_python_installed():
    try:
        subprocess.run(
            ["python", "--version"],
            capture_output=True,
            text=True,
            check=True
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def install_python():
    if is_python_installed():
        print("✓ Python is already installed")
        return

    print("Installing Python...")

    try:
        subprocess.run(
            [
                "winget",
                "install",
                "--id",
                "Python.Python.3.13",
                "-e",
                "--accept-source-agreements",
                "--accept-package-agreements"
            ],
            check=True
        )

        print("✓ Python installed successfully")

    except FileNotFoundError:
        print("✗ Failed to install Python.")
        print("  Please verify that Winget is installed and available in PATH.")
        print("  Download it from: https://aka.ms/getwinget")

    except PermissionError:
        print("✗ Failed to install Python.")
        print("  Permission denied. Please run the terminal as Administrator.")

    except subprocess.CalledProcessError as e:
        print(f"✗ Failed to install Python (exit code {e.returncode}).")
        print("  Please verify your internet connection and try again.")
        print("  You can also install Python manually from: https://www.python.org")

    except OSError as e:
        print(f"✗ Failed to install Python.")
        print(f"  System error: {e}")
