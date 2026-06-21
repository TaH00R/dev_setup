import os
import shutil
import subprocess

import requests

DOWNLOAD_DIR = "downloads"

VSCODE_URL = "https://update.code.visualstudio.com/latest/win32-x64-user/stable"

INSTALLER_NAME = os.path.join(
    DOWNLOAD_DIR,
    "VSCodeSetup.exe"
)


class VSCodeInstaller:

    @staticmethod
    def is_installed():
        return shutil.which("code") is not None

    @staticmethod
    def download():
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)

        print("[+] Downloading VS Code...")

        try:
            response = requests.get(VSCODE_URL, stream=True, timeout=30)
            response.raise_for_status()

            with open(INSTALLER_NAME, "wb") as file:
                for chunk in response.iter_content(chunk_size=8192):
                    file.write(chunk)

            print("[✓] Download complete")

        except requests.ConnectionError:
            raise ConnectionError(
                "Failed to download VS Code. "
                "Please verify your internet connection and try again."
            )

        except requests.Timeout:
            raise TimeoutError(
                "Download timed out. "
                "Please check your internet connection and try again."
            )

        except requests.HTTPError as e:
            raise RuntimeError(
                f"Failed to download VS Code (HTTP {e.response.status_code}). "
                "The download URL may be unavailable. "
                "Please download manually from: https://code.visualstudio.com"
            )

    @staticmethod
    def install():
        print("[+] Installing VS Code...")

        try:
            subprocess.run(
                [
                    INSTALLER_NAME,
                    "/VERYSILENT",
                    "/MERGETASKS=!runcode"
                ],
                check=True
            )

            print("[✓] VS Code installed")

        except FileNotFoundError:
            raise FileNotFoundError(
                "VS Code installer not found. "
                "The download may have failed. Please try again."
            )

        except PermissionError:
            raise PermissionError(
                "Permission denied during VS Code installation. "
                "Please run the terminal as Administrator."
            )

        except subprocess.CalledProcessError as e:
            raise RuntimeError(
                f"VS Code installation failed (exit code {e.returncode}). "
                "Please try installing manually from: https://code.visualstudio.com"
            )

    @staticmethod
    def install_extensions():

        extensions = [
            "ms-vscode.cpptools",
            "ms-vscode.cmake-tools",
            "usernamehw.errorlens"
        ]

        print("[+] Installing VS Code extensions...")

        for extension in extensions:
            subprocess.run(
                [
                    "code",
                    "--install-extension",
                    extension
                ]
            )

        print("[✓] Extensions installed")

    @staticmethod
    def verify():
        try:
            result = subprocess.run(
                ["code", "--version"],
                capture_output=True,
                text=True
            )

            return result.returncode == 0

        except (subprocess.CalledProcessError, FileNotFoundError):
            return False

    @classmethod
    def setup(cls):

        if cls.is_installed():
            print("[✓] VS Code already installed")
            return

        try:
            cls.download()
            cls.install()
        except (ConnectionError, TimeoutError, RuntimeError, FileNotFoundError, PermissionError) as e:
            print(f"✗ {e}")
            return

        try:
            cls.install_extensions()
        except Exception:
            print("[!] Could not install extensions")

        print("[✓] VS Code setup finished")
