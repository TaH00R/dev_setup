"""Tests for the system information command.

Run from the repository root with:

    python -m unittest discover -s tests
"""
import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

import system_info


class GetSystemInfoTests(unittest.TestCase):

    def test_reports_expected_keys(self):
        info = system_info.get_system_info()
        self.assertEqual(
            set(info),
            {"os", "architecture", "python_version", "winget_available"},
        )

    def test_python_version_is_dotted(self):
        info = system_info.get_system_info()
        self.assertRegex(info["python_version"], r"^\d+\.\d+\.\d+")

    def test_winget_available_reflects_path_lookup(self):
        with mock.patch("system_info.shutil.which", return_value="/usr/bin/winget"):
            self.assertTrue(system_info.get_system_info()["winget_available"])
        with mock.patch("system_info.shutil.which", return_value=None):
            self.assertFalse(system_info.get_system_info()["winget_available"])


class GetInstalledToolsTests(unittest.TestCase):

    def test_marks_present_and_absent_tools(self):
        def fake_which(command):
            return "/usr/bin/git" if command == "git" else None

        with mock.patch("system_info.shutil.which", side_effect=fake_which):
            tools = {
                name: is_installed
                for name, is_installed, _version in system_info.get_installed_tools()
            }
        self.assertTrue(tools["Git"])
        self.assertFalse(tools["GCC"])
        self.assertEqual(len(tools), len(system_info.DEV_TOOLS))


class ShowSystemInfoTests(unittest.TestCase):

    def test_output_contains_sections(self):
        buffer = io.StringIO()
        with mock.patch("system_info.shutil.which", return_value=None), \
                redirect_stdout(buffer):
            system_info.show_system_info()
        output = buffer.getvalue()
        self.assertIn("System Information", output)
        self.assertIn("Architecture:", output)
        self.assertIn("Installed Tools:", output)
        self.assertIn("✗ Git", output)


if __name__ == "__main__":
    unittest.main()
