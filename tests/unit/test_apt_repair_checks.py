"""Repair qualification uses fake native commands and a real recovery database."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from models.errors import ErrorCode, OperationError
from models.planning import utc_now
from persistence.store import StateStore
from scripts.apt_dpkg_checks import exercise_apt_dpkg
from scripts.apt_repair_checks import exercise_repair
from scripts.qualify_native import main
from system.commands import CommandResult


class APTRepairTests(unittest.TestCase):
    def test_unrelated_recovery_record_cannot_authorize_fixture_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            f = self.fixture(root)

            def unrelated(services, checks, recovery, record, plan):
                return exercise_repair(
                    services,
                    checks,
                    recovery,
                    replace(record, plan_id="different-plan"),
                    plan,
                )

            with (
                mock.patch(
                    "scripts.apt_repair_checks.exercise_repair", side_effect=unrelated
                ),
                self.assertRaises(ValueError),
            ):
                self.run_profile(f, {}, root / "state.sqlite3")
            self.assertEqual(len(f.mutations), 1)

    def fixture(self, root, *, fail=False, wrong_version=False):
        from tests.unit.test_apt_dpkg_checks import DPKGProfileTests

        f = DPKGProfileTests().fixture(root)
        original = f.runner.run.side_effect

        def run(command, **kwargs):
            if command.mutates:
                self.assertTrue(kwargs["approval"].permits(command))
            if "--configure" in command.argv:
                f.mutations.append(command)
                now = utc_now()
                return CommandResult(
                    command.preview(),
                    now,
                    now,
                    1 if fail else 0,
                    error=OperationError(ErrorCode.COMMAND_FAILED, "Repair failed")
                    if fail
                    else None,
                )
            if "--reinstall" in command.argv:
                f.partial = False
                result = original(command, **kwargs)
                if wrong_version:
                    f.installed.clear()
                return result
            return original(command, **kwargs)

        f.runner.run.side_effect = run
        return f

    def run_profile(self, f, checks, path):
        with (
            mock.patch(
                "scripts.apt_dpkg_checks.pause_owned_dpkg",
                return_value=(1000000001, 1000000002),
            ),
            mock.patch("scripts.apt_dpkg_checks.no_live_group", return_value=True),
        ):
            exercise_apt_dpkg(f.services, checks, path, repair=True)

    def test_repair_reinspection_acknowledgement_and_fresh_actual_install_remove(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            f = self.fixture(root)
            checks = {}
            self.run_profile(f, checks, root / "state.sqlite3")
            self.assertEqual(checks["phase"], "complete")
            self.assertEqual(checks["post_repair_fresh_install_remove"], "passed")
            self.assertEqual(
                checks["retry_after_repair_before_acknowledgement"], "rejected"
            )
            self.assertEqual(len(f.mutations), 6)
            configure, reinstall = f.mutations[1:3]
            self.assertEqual(
                configure.argv, (str(root / "dpkg"), "--configure", "--pending")
            )
            self.assertEqual(configure.timeout_seconds, 180)
            self.assertEqual(reinstall.argv[-1], "cmake=2.0")
            self.assertIn("--reinstall", reinstall.argv)
            self.assertTrue(configure.launch_files)
            self.assertTrue(reinstall.launch_files)
            self.assertFalse(f.installed)
            store = StateStore(root / "state.sqlite3")
            try:
                records = store.load_lifecycle()
                cancelled = next(r for r in records if r.state.value == "cancelled")
                self.assertFalse(cancelled.needs_recovery)
                self.assertEqual(cancelled.verification, "changed")
                self.assertIsNotNone(cancelled.reconciled_at)
                self.assertEqual(sum(r.state.value == "succeeded" for r in records), 3)
            finally:
                store.close()

    def test_failed_configuration_stops_reinstall_and_preserves_unresolved_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            f = self.fixture(root, fail=True)
            checks = {}
            with self.assertRaises(ValueError):
                self.run_profile(f, checks, root / "state.sqlite3")
            self.assertEqual(checks["phase"], "native_repair_configure_pending")
            self.assertEqual(checks["native_repair"], "started_explicit_fixture_repair")
            self.assertFalse(any("--reinstall" in c.argv for c in f.mutations))
            store = StateStore(root / "state.sqlite3")
            try:
                self.assertTrue(store.load_lifecycle()[0].needs_recovery)
            finally:
                store.close()

    def test_repair_exit_zero_without_reviewed_inventory_never_enables_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            f = self.fixture(root, wrong_version=True)
            checks = {}
            with self.assertRaises(ValueError):
                self.run_profile(f, checks, root / "state.sqlite3")
            self.assertNotIn("post_repair_reconciliation", checks)
            self.assertEqual(len(f.mutations), 3)

    def test_new_profile_requires_explicit_disposable_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            with (
                mock.patch("scripts.qualify_native.CommandRunner") as runner,
                mock.patch("builtins.print"),
            ):
                self.assertEqual(
                    main(
                        [
                            "--backend",
                            "apt",
                            "--apt-dpkg-repair",
                            "--output",
                            str(output),
                        ]
                    ),
                    2,
                )
            self.assertFalse(output.exists())
            runner.assert_not_called()
