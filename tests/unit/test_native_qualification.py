"""Opt-in harness contracts are tested entirely with fake application services."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from models.platform import (
    Architecture,
    LinuxDistribution,
    LinuxFamily,
    OperatingSystem,
    Platform,
)
from scripts.qualify_native import disposable_guard, exercise, main, manager_version
from tests.fixtures.desktop import DesktopFixture


class NativeQualificationTests(unittest.TestCase):
    def test_guard_requires_hosted_scope_approval_and_exact_platform(self):
        ubuntu = Platform(
            OperatingSystem.LINUX,
            Architecture.X86_64,
            LinuxDistribution("ubuntu", LinuxFamily.DEBIAN, version="24.04"),
        )
        mac = Platform(OperatingSystem.MACOS, Architecture.ARM64)
        hosted = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted"}
        disposable_guard(
            "apt", ubuntu, hosted, container=True, root=True, approved=True
        )
        disposable_guard(
            "homebrew", mac, hosted, container=False, root=False, approved=True
        )
        for backend, host, env, container, root, approved in (
            ("apt", ubuntu, hosted, True, True, False),
            ("apt", ubuntu, {}, True, True, True),
            (
                "apt",
                ubuntu,
                {**hosted, "RUNNER_ENVIRONMENT": "self-hosted"},
                True,
                True,
                True,
            ),
            ("apt", ubuntu, hosted, False, True, True),
            ("homebrew", mac, hosted, False, True, True),
            ("homebrew", ubuntu, hosted, True, False, True),
        ):
            with (
                self.subTest(backend=backend, host=host, env=env),
                self.assertRaises(ValueError),
            ):
                disposable_guard(
                    backend,
                    host,
                    env,
                    container=container,
                    root=root,
                    approved=approved,
                )

    def test_guard_rejection_precedes_file_creation_or_command_construction(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "must-not-exist.json"
            with (
                mock.patch(
                    "scripts.qualify_native.disposable_guard",
                    side_effect=ValueError("private-token"),
                ),
                mock.patch("scripts.qualify_native.CommandRunner") as runner,
                mock.patch("builtins.print") as printed,
            ):
                result = main(
                    [
                        "--backend",
                        "apt",
                        "--approve-native-changes",
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(result, 2)
            self.assertFalse(output.exists())
            runner.assert_not_called()
            self.assertNotIn("private-token", repr(printed.call_args_list))

    def test_smoke_exercises_real_workflow_logic_but_does_not_claim_upgrade_qualification(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            fixture = DesktopFixture(Path(directory))
            fixture.install()  # The explicit disposable scope includes cleanup.
            checks = {}
            exercise(fixture.services, "git", checks)
            self.assertEqual(checks["phase"], "complete")
            self.assertEqual(checks["install_verify_refresh"], "passed")
            self.assertEqual(checks["stale_inventory_plan"], "passed")
            self.assertEqual(checks["real_version_upgrade"], "not_tested")
            self.assertEqual(checks["mid_command_cancellation"], "not_tested")
            self.assertEqual(
                [command.argv[1] for command in fixture.mutations],
                ["uninstall", "install", "uninstall"],
            )
            self.assertFalse(fixture.installed)

    def test_smoke_stops_on_failed_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = DesktopFixture(Path(directory))
            fixture.mutate = False
            checks = {}
            with self.assertRaises(ValueError):
                exercise(fixture.services, "git", checks)
            self.assertEqual(checks["phase"], "install_and_verify")
            self.assertNotIn("remove_verify", checks)

    def test_manager_version_extracts_only_bounded_native_version(self):
        self.assertEqual(
            manager_version("apt", "apt 2.8.3 (amd64)\nother details"), "2.8.3"
        )
        self.assertEqual(
            manager_version("homebrew", "Homebrew 4.6.2\nlocal paths"), "4.6.2"
        )
        with self.assertRaises(ValueError) as caught:
            manager_version("apt", "password=private-token")
        self.assertNotIn("private-token", str(caught.exception))
