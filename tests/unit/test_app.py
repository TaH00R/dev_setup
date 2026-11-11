"""Exercise the documented entry point without running package operations."""

import io
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from app.main import main


class AppTests(unittest.TestCase):
    def test_gui_command_loads_optional_bootstrap_only_when_requested(self):
        with mock.patch("app.bootstrap.launch", return_value=0) as launch:
            self.assertEqual(main(["gui"]), 0)
        launch.assert_called_once_with()

    def test_missing_qt_has_actionable_message_and_leaves_catalog_usable(self):
        real_import = __import__

        def without_qt(name, *args, **kwargs):
            if name == "app.bootstrap":
                raise ModuleNotFoundError("No module named PySide6", name="PySide6")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=without_qt):
            status, _, errors = self.run_app(["gui"])
            self.assertEqual(status, 1)
            self.assertIn("requirements-gui.txt", errors)
            self.assertEqual(self.run_app(["catalog", "validate"])[0], 0)

    def run_app(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with (
            redirect_stdout(stdout),
            redirect_stderr(stderr),
            mock.patch(
                "subprocess.Popen",
                side_effect=AssertionError(
                    "read-only commands must not spawn processes"
                ),
            ),
        ):
            status = main(args)
        return status, stdout.getvalue(), stderr.getvalue()

    def test_no_arguments_show_working_commands_without_starting_installers(self):
        status, output, errors = self.run_app([])
        self.assertEqual(status, 0)
        self.assertIn("catalog", output)
        self.assertIn("gui", output)
        self.assertEqual(errors, "")

    def test_catalog_validation_reports_bundled_metadata(self):
        status, output, errors = self.run_app(["catalog", "validate"])
        self.assertEqual(status, 0)
        self.assertIn("14 packages and 39 reviewed mappings", output)
        self.assertEqual(errors, "")

    def test_search_preserves_native_identifiers_and_platform_limits(self):
        status, output, errors = self.run_app(["catalog", "search", "git"])
        self.assertEqual(status, 0)
        self.assertIn("Git (git)", output)
        self.assertIn("windows / winget / winget: Git.Git", output)
        self.assertIn("installability still need a plan", output)
        self.assertEqual(errors, "")

    def test_category_filter_and_empty_results(self):
        status, output, _ = self.run_app(["catalog", "search", "--category", "editors"])
        self.assertEqual(status, 0)
        self.assertIn("Visual Studio Code", output)
        self.assertNotIn("Git (git)", output)
        self.assertEqual(
            self.run_app(["catalog", "search", "no-such-package-123"])[1],
            "No packages found.\n",
        )

    def test_invalid_catalog_fails_with_useful_exit_status(self):
        status, _, errors = self.run_app(
            ["catalog", "validate", "--directory", "missing-test-catalog"]
        )
        self.assertEqual(status, 1)
        self.assertIn("Catalog validation failed", errors)

    def test_legacy_menu_requires_explicit_command(self):
        module = types.ModuleType("app.legacy.cli")
        module.main = mock.Mock()
        with mock.patch.dict("sys.modules", {"app.legacy.cli": module}):
            self.run_app(["catalog", "validate"])
            module.main.assert_not_called()
            self.assertEqual(main(["legacy"]), 0)
            module.main.assert_called_once_with()

    def test_missing_legacy_dependencies_do_not_break_the_catalog(self):
        real_import = __import__

        def without_legacy_dependencies(name, *args, **kwargs):
            if name == "app.legacy.cli":
                raise ModuleNotFoundError("No module named distro", name="distro")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=without_legacy_dependencies):
            self.assertEqual(self.run_app(["catalog", "validate"])[0], 0)
            status, _, errors = self.run_app(["legacy"])
        self.assertEqual(status, 1)
        self.assertIn("python -m pip install -r requirements.txt", errors)
