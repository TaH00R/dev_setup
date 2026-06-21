"""Tests that the winget-based installers degrade gracefully on failure.

Run from the repository root with:

    python -m unittest discover -s tests
"""
import io
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest import mock

from installers import git_installer, python_installer


class GitInstallerTests(unittest.TestCase):

    def test_reports_missing_winget_without_crashing(self):
        buffer = io.StringIO()
        with mock.patch.object(git_installer, "is_git_installed", return_value=False), \
                mock.patch.object(git_installer, "winget_available", return_value=False), \
                redirect_stdout(buffer):
            git_installer.install_git()
        output = buffer.getvalue()
        self.assertIn("Failed to install Git", output)
        self.assertIn("Winget was not found", output)

    def test_reports_failed_install_without_crashing(self):
        buffer = io.StringIO()
        error = subprocess.CalledProcessError(returncode=1, cmd=["winget"])
        with mock.patch.object(git_installer, "is_git_installed", return_value=False), \
                mock.patch.object(git_installer, "winget_available", return_value=True), \
                mock.patch.object(git_installer.subprocess, "run", side_effect=error), \
                redirect_stdout(buffer):
            git_installer.install_git()
        self.assertIn("Failed to install Git", buffer.getvalue())

    def test_skips_when_already_installed(self):
        buffer = io.StringIO()
        with mock.patch.object(git_installer, "is_git_installed", return_value=True), \
                redirect_stdout(buffer):
            git_installer.install_git()
        self.assertIn("already installed", buffer.getvalue())


class PythonInstallerTests(unittest.TestCase):

    def test_reports_missing_winget_without_crashing(self):
        buffer = io.StringIO()
        with mock.patch.object(python_installer, "is_python_installed", return_value=False), \
                mock.patch.object(python_installer, "winget_available", return_value=False), \
                redirect_stdout(buffer):
            python_installer.install_python()
        output = buffer.getvalue()
        self.assertIn("Failed to install Python", output)
        self.assertIn("Winget was not found", output)


if __name__ == "__main__":
    unittest.main()
