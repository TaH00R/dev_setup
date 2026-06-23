"""Tests that the Node.js installer degrades gracefully on failure.

Run from the repository root with:

    python -m unittest discover -s tests
"""
import io
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest import mock

from installers.node_installer import NodeInstaller


class NodeInstallerTests(unittest.TestCase):

    def test_skips_when_already_installed(self):
        buffer = io.StringIO()
        with mock.patch.object(NodeInstaller, "_is_node_installed", return_value=True), \
                redirect_stdout(buffer):
            NodeInstaller.setup()
        self.assertIn("already installed", buffer.getvalue())

    def test_reports_missing_winget_without_crashing(self):
        buffer = io.StringIO()
        with mock.patch.object(NodeInstaller, "_is_node_installed", return_value=False), \
                mock.patch("installers.node_installer.winget_available", return_value=False), \
                redirect_stdout(buffer):
            NodeInstaller.setup()
        output = buffer.getvalue()
        self.assertIn("Failed to install Node.js", output)
        self.assertIn("Winget was not found", output)

    def test_reports_failed_install_without_crashing(self):
        buffer = io.StringIO()
        error = subprocess.CalledProcessError(returncode=1, cmd=["winget"])
        with mock.patch.object(NodeInstaller, "_is_node_installed", return_value=False), \
                mock.patch("installers.node_installer.winget_available", return_value=True), \
                mock.patch("installers.node_installer.subprocess.run", side_effect=error), \
                redirect_stdout(buffer):
            NodeInstaller.setup()
        self.assertIn("Failed to install Node.js", buffer.getvalue())

    def test_success_path(self):
        buffer = io.StringIO()
        with mock.patch.object(NodeInstaller, "_is_node_installed", return_value=False), \
                mock.patch("installers.node_installer.winget_available", return_value=True), \
                mock.patch("installers.node_installer.subprocess.run"), \
                redirect_stdout(buffer):
            NodeInstaller.setup()
        self.assertIn("Node.js installed successfully", buffer.getvalue())

    def test_linux_download_uses_node_packages(self):
        expected_commands = {
            "debian": ["sudo", "apt", "install", "-y", "nodejs", "npm"],
            "arch": ["sudo", "pacman", "-S", "--noconfirm", "nodejs", "npm"],
            "fedora": ["sudo", "dnf", "install", "-y", "nodejs", "npm"],
            "opensuse": ["sudo", "zypper", "install", "-y", "nodejs", "npm"],
        }

        for distro_name, expected_command in expected_commands.items():
            with self.subTest(distro=distro_name), \
                    mock.patch("installers.node_installer.OS", "Linux"), \
                    mock.patch("installers.node_installer.dist", distro_name), \
                    mock.patch("installers.node_installer.subprocess.run") as run_mock:
                NodeInstaller.download()

            run_mock.assert_any_call(expected_command)


if __name__ == "__main__":
    unittest.main()
