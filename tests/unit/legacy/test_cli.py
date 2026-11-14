"""Preserve the legacy menu without invoking real installer operations."""

import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

from app.legacy import cli as main
from models.errors import ErrorCode
from system.coordination import CoordinationError


class LegacyCLITests(unittest.TestCase):
    def setUp(self):
        self.git = mock.Mock()
        self.python = mock.Mock()
        patches = [
            mock.patch.dict(
                main.INSTALLERS,
                {"1": ("Git", self.git), "2": ("Python", self.python)},
                clear=True,
            ),
            mock.patch("app.legacy.cli.show_system_info"),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def run_menu(self, choices):
        output = io.StringIO()
        with mock.patch("builtins.input", side_effect=choices), redirect_stdout(output):
            main.main()
        return output.getvalue()

    def test_invalid_choice_then_exit_runs_no_installer(self):
        output = self.run_menu(["invalid", "0"])
        self.assertIn("Invalid choice", output)
        self.assertIn("Goodbye!", output)
        self.git.assert_not_called()
        self.python.assert_not_called()

    def test_selected_tool_dispatches_only_that_installer(self):
        output = self.run_menu([" 1 ", "0"])
        self.git.assert_called_once_with()
        self.python.assert_not_called()
        self.assertIn("Git setup complete!", output)

    def test_install_everything_dispatches_in_menu_order(self):
        order = mock.Mock()
        order.attach_mock(self.git, "git")
        order.attach_mock(self.python, "python")
        self.run_menu(["9", "0"])
        self.assertEqual(order.mock_calls, [mock.call.git(), mock.call.python()])

    def test_system_information_does_not_dispatch_installers(self):
        self.run_menu(["10", "0"])
        self.assertEqual(main.show_system_info.call_count, 2)
        self.git.assert_not_called()
        self.python.assert_not_called()

    def test_busy_native_guard_prevents_dispatch_and_does_not_claim_completion(self):
        with mock.patch("app.legacy.cli.NativeCoordinator") as coordinator:
            coordinator.return_value.hold.return_value.__enter__.side_effect = (
                CoordinationError(ErrorCode.BUSY, "Another task holds native access.")
            )
            output = self.run_menu(["1", "0"])
        self.git.assert_not_called()
        self.assertNotIn("Git setup complete!", output)
        self.assertIn("Another task holds native access", output)

    def test_install_everything_stops_on_busy_guard_without_retrying_next_tool(self):
        with mock.patch("app.legacy.cli.NativeCoordinator") as coordinator:
            coordinator.return_value.hold.return_value.__enter__.side_effect = (
                CoordinationError(ErrorCode.BUSY, "Native access busy.")
            )
            output = self.run_menu(["9", "0"])
        self.git.assert_not_called()
        self.python.assert_not_called()
        self.assertNotIn("Setup Complete!", output)


if __name__ == "__main__":
    unittest.main()
