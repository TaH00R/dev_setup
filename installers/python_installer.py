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
    except Exception:
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

    except subprocess.CalledProcessError:
        print("✗ Failed to install Python")