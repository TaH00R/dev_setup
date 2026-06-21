"""Tests that the download-based installers degrade gracefully on failure.

gcc_installer reads USERPROFILE at import time (Windows-only), so we provide a
fallback before importing it, allowing the suite to run on any platform.

Run from the repository root with:

    python -m unittest discover -s tests
"""
import os

os.environ.setdefault("USERPROFILE", os.path.expanduser("~"))

import io  # noqa: E402
import subprocess  # noqa: E402
import unittest  # noqa: E402
import zipfile  # noqa: E402
from contextlib import redirect_stdout  # noqa: E402
from unittest import mock  # noqa: E402

import requests  # noqa: E402

from installers.gcc_installer import GCCInstaller  # noqa: E402
from installers.vscode_installer import VSCodeInstaller  # noqa: E402


class GCCInstallerTests(unittest.TestCase):

    def test_network_failure_during_download(self):
        buffer = io.StringIO()
        with mock.patch.object(GCCInstaller, "is_installed", return_value=False), \
                mock.patch.object(GCCInstaller, "download",
                                  side_effect=requests.exceptions.ConnectionError()), \
                redirect_stdout(buffer):
            GCCInstaller.setup()
        output = buffer.getvalue()
        self.assertIn("Failed to download GCC", output)
        self.assertIn("Could not reach", output)

    def test_corrupted_archive(self):
        buffer = io.StringIO()
        with mock.patch.object(GCCInstaller, "is_installed", return_value=False), \
                mock.patch.object(GCCInstaller, "download"), \
                mock.patch.object(GCCInstaller, "extract", side_effect=zipfile.BadZipFile()), \
                redirect_stdout(buffer):
            GCCInstaller.setup()
        self.assertIn("corrupted", buffer.getvalue())

    def test_missing_binary_does_not_raise(self):
        buffer = io.StringIO()
        with mock.patch.object(GCCInstaller, "is_installed", return_value=False), \
                mock.patch.object(GCCInstaller, "download"), \
                mock.patch.object(GCCInstaller, "extract"), \
                mock.patch.object(GCCInstaller, "find_bin_folder", return_value=None), \
                redirect_stdout(buffer):
            GCCInstaller.setup()
        self.assertIn("binaries were not found", buffer.getvalue())

    def test_path_update_failure_is_reported(self):
        buffer = io.StringIO()
        error = subprocess.CalledProcessError(returncode=1, cmd=["setx"])
        with mock.patch.object(GCCInstaller, "is_installed", return_value=False), \
                mock.patch.object(GCCInstaller, "download"), \
                mock.patch.object(GCCInstaller, "extract"), \
                mock.patch.object(GCCInstaller, "find_bin_folder", return_value="C:/gcc/bin"), \
                mock.patch.object(GCCInstaller, "add_to_path", side_effect=error), \
                redirect_stdout(buffer):
            GCCInstaller.setup()
        output = buffer.getvalue()
        self.assertIn("updating PATH failed", output)
        self.assertIn("C:/gcc/bin", output)


class VSCodeInstallerTests(unittest.TestCase):

    def test_network_timeout_during_download(self):
        buffer = io.StringIO()
        with mock.patch.object(VSCodeInstaller, "is_installed", return_value=False), \
                mock.patch.object(VSCodeInstaller, "download",
                                  side_effect=requests.exceptions.Timeout()), \
                redirect_stdout(buffer):
            VSCodeInstaller.setup()
        output = buffer.getvalue()
        self.assertIn("Failed to download VS Code", output)
        self.assertIn("timed out", output)

    def test_installer_exit_code_is_reported(self):
        buffer = io.StringIO()
        error = subprocess.CalledProcessError(returncode=2, cmd=["VSCodeSetup.exe"])
        with mock.patch.object(VSCodeInstaller, "is_installed", return_value=False), \
                mock.patch.object(VSCodeInstaller, "download"), \
                mock.patch.object(VSCodeInstaller, "install", side_effect=error), \
                redirect_stdout(buffer):
            VSCodeInstaller.setup()
        self.assertIn("Failed to install VS Code", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
