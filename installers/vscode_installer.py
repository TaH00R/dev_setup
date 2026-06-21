import os
import shutil
import subprocess

import requests

from installers.errors import (
    FILE_PERMISSION_HINT,
    describe_network_error,
    report_failure,
)

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

        response = requests.get(VSCODE_URL, stream=True)
        response.raise_for_status()

        with open(INSTALLER_NAME, "wb") as file:
            for chunk in response.iter_content(chunk_size=8192):
                file.write(chunk)

        print("[✓] Download complete")

    @staticmethod
    def install():
        print("[+] Installing VS Code...")

        subprocess.run(
            [
                INSTALLER_NAME,
                "/VERYSILENT",
                "/MERGETASKS=!runcode"
            ],
            check=True
        )

        print("[✓] VS Code installed")

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

        except Exception:
            return False

    @classmethod
    def setup(cls):

        if cls.is_installed():
            print("[✓] VS Code already installed")
            return

        try:
            cls.download()
        except requests.exceptions.RequestException as error:
            report_failure("Failed to download VS Code", describe_network_error(error))
            return
        except PermissionError:
            report_failure("Failed to install VS Code", FILE_PERMISSION_HINT)
            return
        except OSError as error:
            report_failure("Failed to install VS Code", f"A file system error occurred: {error}")
            return

        try:
            cls.install()
        except subprocess.CalledProcessError as error:
            report_failure(
                "Failed to install VS Code",
                f"The installer exited with code {error.returncode}. "
                "If this is a permissions error, run the terminal as Administrator."
            )
            return
        except PermissionError:
            report_failure("Failed to install VS Code", FILE_PERMISSION_HINT)
            return
        except OSError as error:
            report_failure("Failed to install VS Code", f"A file system error occurred: {error}")
            return

        try:
            cls.install_extensions()
        except Exception:
            print("[!] Could not install extensions")

        print("[✓] VS Code setup finished")