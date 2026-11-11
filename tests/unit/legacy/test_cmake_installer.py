"""Tests that the CMake installer degrades gracefully on failure.

Run from the repository root with:

    python -m unittest discover -s tests
"""
import io
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest import mock

from app.legacy.installers.cmake_installer import CMakeInstaller


class CMakeInstallerTests(unittest.TestCase):

    def setUp(self):
        platform_patch = mock.patch("app.legacy.installers.cmake_installer.OS", "Windows")
        platform_patch.start()
        self.addCleanup(platform_patch.stop)

    def test_skips_when_already_installed(self):
        buffer = io.StringIO()
        with mock.patch.object(CMakeInstaller, "_is_cmake_installed", return_value=True), \
                redirect_stdout(buffer):
            CMakeInstaller.setup()
        self.assertIn("already installed", buffer.getvalue())

    def test_reports_missing_winget_without_crashing(self):
        buffer = io.StringIO()
        with mock.patch.object(CMakeInstaller, "_is_cmake_installed", return_value=False), \
                mock.patch("app.legacy.installers.cmake_installer.winget_available", return_value=False), \
                redirect_stdout(buffer):
            CMakeInstaller.setup()
        output = buffer.getvalue()
        self.assertIn("Failed to install CMake", output)
        self.assertIn("Winget was not found", output)

    def test_reports_failed_install_without_crashing(self):
        buffer = io.StringIO()
        error = subprocess.CalledProcessError(returncode=1, cmd=["winget"])
        with mock.patch.object(CMakeInstaller, "_is_cmake_installed", return_value=False), \
                mock.patch("app.legacy.installers.cmake_installer.winget_available", return_value=True), \
                mock.patch("app.legacy.installers.cmake_installer.subprocess.run", side_effect=error), \
                redirect_stdout(buffer):
            CMakeInstaller.setup()
        self.assertIn("Failed to install CMake", buffer.getvalue())

    def test_success_path(self):
        buffer = io.StringIO()
        with mock.patch.object(CMakeInstaller, "_is_cmake_installed", return_value=False), \
                mock.patch("app.legacy.installers.cmake_installer.winget_available", return_value=True), \
                mock.patch("app.legacy.installers.cmake_installer.subprocess.run"), \
                redirect_stdout(buffer):
            CMakeInstaller.setup()
        self.assertIn("CMake installed successfully", buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
