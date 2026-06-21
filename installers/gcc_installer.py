import os
import shutil
import subprocess
import zipfile

import requests

from installers.errors import (
    FILE_PERMISSION_HINT,
    describe_network_error,
    report_failure,
)

DOWNLOAD_DIR = "downloads"

GCC_URL = (
    "https://github.com/brechtsanders/winlibs_mingw/releases/latest/download/"
    "winlibs-x86_64-posix-seh-gcc.zip"
)

ZIP_NAME = os.path.join(DOWNLOAD_DIR, "gcc.zip")
INSTALL_DIR = os.path.join(
    os.environ["USERPROFILE"],
    "DevSetup",
    "gcc"
)


class GCCInstaller:

    @staticmethod
    def is_installed():
        return shutil.which("gcc") is not None

    @staticmethod
    def download():
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)

        print("[+] Downloading GCC...")

        response = requests.get(GCC_URL, stream=True)
        response.raise_for_status()

        with open(ZIP_NAME, "wb") as file:
            for chunk in response.iter_content(chunk_size=8192):
                file.write(chunk)

        print("[✓] Download complete")

    @staticmethod
    def extract():
        print("[+] Extracting GCC...")

        os.makedirs(INSTALL_DIR, exist_ok=True)

        with zipfile.ZipFile(ZIP_NAME, "r") as zip_ref:
            zip_ref.extractall(INSTALL_DIR)

        print("[✓] Extraction complete")

    @staticmethod
    def find_bin_folder():
        for root, dirs, files in os.walk(INSTALL_DIR):
            if "gcc.exe" in files:
                return root

        return None

    @staticmethod
    def add_to_path(path):
        print("[+] Adding GCC to PATH...")

        subprocess.run(
            f'setx PATH "%PATH%;{path}"',
            shell=True,
            check=True
        )

        print("[✓] PATH updated")

    @staticmethod
    def verify():
        try:
            result = subprocess.run(
                ["gcc", "--version"],
                capture_output=True,
                text=True
            )

            return result.returncode == 0

        except Exception:
            return False

    @classmethod
    def setup(cls):

        if cls.is_installed():
            print("[✓] GCC already installed")
            return

        try:
            cls.download()
            cls.extract()
        except requests.exceptions.RequestException as error:
            report_failure("Failed to download GCC", describe_network_error(error))
            return
        except zipfile.BadZipFile:
            report_failure(
                "Failed to install GCC",
                "The downloaded archive is corrupted or incomplete. "
                "Please try again."
            )
            return
        except PermissionError:
            report_failure("Failed to install GCC", FILE_PERMISSION_HINT)
            return
        except OSError as error:
            report_failure("Failed to install GCC", f"A file system error occurred: {error}")
            return

        bin_path = cls.find_bin_folder()

        if not bin_path:
            report_failure(
                "Failed to install GCC",
                "The GCC binaries were not found in the extracted files. "
                "The download may be incomplete; please try again."
            )
            return

        try:
            cls.add_to_path(bin_path)
        except (subprocess.CalledProcessError, OSError) as error:
            report_failure(
                "GCC was installed, but updating PATH failed",
                "Add this folder to your PATH manually:\n"
                f"{bin_path}\n"
                f"Reason: {error}"
            )
            return

        print("\n[!] Open a new terminal after installation.\n")

        print("[✓] GCC installation finished")