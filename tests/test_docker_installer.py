"""Tests that the Docker installer degrades gracefully on failure.

Run from the repository root with:

    python -m unittest discover -s tests
"""

import io
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest import mock

from installers.docker_installer import DockerInstaller


class DockerInstallerTests(unittest.TestCase):

    def setUp(self):
        platform_patch = mock.patch("installers.docker_installer.OS", "Windows")
        platform_patch.start()
        self.addCleanup(platform_patch.stop)

    def test_skips_when_already_installed(self):
        buffer = io.StringIO()

        with mock.patch.object(
            DockerInstaller,
            "_is_docker_installed",
            return_value=True,
        ), redirect_stdout(buffer):

            DockerInstaller.setup()

        self.assertIn("already installed", buffer.getvalue())

    def test_reports_missing_winget_without_crashing(self):
        buffer = io.StringIO()

        with mock.patch.object(
            DockerInstaller,
            "_is_docker_installed",
            return_value=False,
        ), mock.patch(
            "installers.docker_installer.winget_available",
            return_value=False,
        ), redirect_stdout(buffer):

            DockerInstaller.setup()

        output = buffer.getvalue()

        self.assertIn("Failed to install Docker", output)
        self.assertIn("Package manager", output)

    def test_reports_failed_install_without_crashing(self):
        buffer = io.StringIO()

        error = subprocess.CalledProcessError(
            returncode=1,
            cmd=["winget"],
        )

        with mock.patch.object(
            DockerInstaller,
            "_is_docker_installed",
            return_value=False,
        ), mock.patch(
            "installers.docker_installer.winget_available",
            return_value=True,
        ), mock.patch(
            "installers.docker_installer.subprocess.run",
            side_effect=error,
        ), redirect_stdout(buffer):

            DockerInstaller.setup()

        self.assertIn("Failed to install Docker", buffer.getvalue())

    def test_success_path(self):
        buffer = io.StringIO()

        with mock.patch.object(
            DockerInstaller,
            "_is_docker_installed",
            side_effect=[False, True],
        ), mock.patch(
            "installers.docker_installer.winget_available",
            return_value=True,
        ), mock.patch(
            "installers.docker_installer.subprocess.run",
        ), redirect_stdout(buffer):

            DockerInstaller.setup()

        self.assertIn("Docker installed successfully", buffer.getvalue())

    def test_linux_download_uses_docker_packages(self):
        expected_commands = {
            "debian": ["sudo", "apt", "install", "-y", "docker.io"],
            "arch": ["sudo", "pacman", "-S", "--noconfirm", "docker"],
            "fedora": ["sudo", "dnf", "install", "-y", "docker"],
            "opensuse": ["sudo", "zypper", "install", "-y", "docker"],
        }

        for distro_name, expected_command in expected_commands.items():
            with self.subTest(distro=distro_name), \
                mock.patch("installers.docker_installer.OS", "Linux"), \
                mock.patch("installers.docker_installer.dist", distro_name), \
                mock.patch("installers.docker_installer.subprocess.run") as run_mock:

                DockerInstaller.download()

            run_mock.assert_any_call(expected_command, check=True)


if __name__ == "__main__":
    unittest.main()