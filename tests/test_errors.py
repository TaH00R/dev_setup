"""Tests for the shared installer error-handling helpers.

Run from the repository root with:

    python -m unittest discover -s tests
"""
import io
import subprocess
import unittest
from contextlib import redirect_stdout
from unittest import mock

import requests

from installers import errors


class WingetAvailableTests(unittest.TestCase):

    def test_true_when_on_path(self):
        with mock.patch("installers.errors.shutil.which", return_value="C:/winget.exe"):
            self.assertTrue(errors.winget_available())

    def test_false_when_missing(self):
        with mock.patch("installers.errors.shutil.which", return_value=None):
            self.assertFalse(errors.winget_available())


class DescribeWingetErrorTests(unittest.TestCase):

    def test_missing_winget_mentions_path(self):
        message = errors.describe_winget_error(FileNotFoundError())
        self.assertIn("Winget was not found", message)
        self.assertIn("PATH", message)

    def test_permission_error_suggests_administrator(self):
        message = errors.describe_winget_error(PermissionError())
        self.assertIn("Administrator", message)

    def test_called_process_error_includes_exit_code(self):
        error = subprocess.CalledProcessError(returncode=5, cmd=["winget"])
        message = errors.describe_winget_error(error)
        self.assertIn("5", message)
        self.assertIn("network", message)

    def test_unknown_error_falls_back_to_text(self):
        self.assertEqual(errors.describe_winget_error(ValueError("boom")), "boom")


class DescribeNetworkErrorTests(unittest.TestCase):

    def test_timeout(self):
        message = errors.describe_network_error(requests.exceptions.Timeout())
        self.assertIn("timed out", message)

    def test_connection_error(self):
        message = errors.describe_network_error(requests.exceptions.ConnectionError())
        self.assertIn("Could not reach", message)

    def test_http_error_includes_detail(self):
        message = errors.describe_network_error(requests.exceptions.HTTPError("404 Not Found"))
        self.assertIn("404", message)

    def test_unknown_error_falls_back_to_text(self):
        self.assertEqual(errors.describe_network_error(ValueError("boom")), "boom")


class ReportFailureTests(unittest.TestCase):

    def test_prints_action_and_indented_hint(self):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            errors.report_failure("Failed to install Git", "line one\nline two")
        self.assertEqual(
            buffer.getvalue(),
            "✗ Failed to install Git\n  line one\n  line two\n",
        )


if __name__ == "__main__":
    unittest.main()
