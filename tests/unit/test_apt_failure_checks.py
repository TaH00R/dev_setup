"""Controlled fault-profile regressions; fake native runner, real lifecycle SQLite."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from models.errors import ErrorCode, OperationError
from models.planning import utc_now
from package_managers.base import BackendAction
from persistence.store import StateStore
from scripts.apt_failure_checks import (
    DownloadLimitedAPT,
    active_apt_child,
    exercise_apt_failures,
    no_live_group,
)
from services.desktop import DesktopServices
from services.records import PackageRequest
from system.commands import CommandResult, OutputSnapshot


class APTFailureProfileTests(unittest.TestCase):
    def fixture(self, root):
        # Import locally so unittest does not discover the other TestCase twice.
        from tests.unit.test_native_qualification import NativeQualificationTests

        fixture = NativeQualificationTests().apt_fixture(root, "cmake")
        base = fixture.services
        fixture.adapter = DownloadLimitedAPT(base.host, find=fixture.adapter.find)
        fixture.services = DesktopServices(
            base.catalog,
            base.host,
            (fixture.adapter,),
            (fixture,),
            fixture.runner,
            require_native_qualification=False,
        )
        original = fixture.runner.run.side_effect

        def run(command, **kwargs):
            if (
                command.mutates
                and "Acquire::http::Dl-Limit=8" in command.argv
                and "install" in command.argv
            ):
                observer = kwargs.get("observe")
                if observer:
                    observer(
                        OutputSnapshot("stdout", "Get:1 reviewed download [1000 kB]\n")
                    )
                cancel = kwargs.get("cancel")
                if cancel and cancel.is_set():
                    now = utc_now()
                    return CommandResult(
                        command.preview(),
                        now,
                        now,
                        -15,
                        error=OperationError(ErrorCode.CANCELLED, "Fixture cancelled."),
                    )
            return original(command, **kwargs)

        fixture.runner.run.side_effect = run
        return fixture, original

    def test_fixed_throttle_is_after_native_executable_and_only_on_mutations(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture, _ = self.fixture(Path(directory))
            plan = fixture.services.planner.preview((PackageRequest("cmake"),))
            command = plan.prepared[0].command
            executable = fixture.adapter.find("apt-get")
            position = command.argv.index(executable)
            self.assertEqual(
                command.argv[position + 1 : position + 3],
                ("-o", "Acquire::http::Dl-Limit=8"),
            )
            spec = plan.plan.items[0].spec
            self.assertNotIn(
                "Acquire::http::Dl-Limit=8",
                fixture.adapter.build(BackendAction.SHOW, spec=spec).argv,
            )

    def test_witness_accepts_only_a_live_direct_apt_child(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            children = proc / "42" / "task" / "42"
            children.mkdir(parents=True)
            (children / "children").write_text("100 101 invalid")
            for pid in (100, 101):
                child = proc / str(pid)
                child.mkdir()
                (child / "comm").write_text("apt-get\n")
                (child / "status").write_text("State:\tS (sleeping)\nPPid:\t99\n")
            (proc / "101" / "status").write_text("State:\tS (sleeping)\nPPid:\t42\n")
            with mock.patch("scripts.apt_failure_checks.os.getpid", return_value=42):
                self.assertEqual(active_apt_child(proc), 101)
                (proc / "101" / "status").write_text("State:\tZ (zombie)\nPPid:\t42\n")
                self.assertIsNone(active_apt_child(proc))
                (proc / "101" / "status").unlink()
                self.assertIsNone(active_apt_child(proc))

    def test_cancel_reopen_blocked_retry_reconcile_and_zero_exit_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture, _ = self.fixture(root)
            checks = {}
            with (
                mock.patch(
                    "scripts.apt_failure_checks.active_apt_child",
                    return_value=1000000001,
                ),
                mock.patch(
                    "scripts.apt_failure_checks.no_live_group", return_value=True
                ),
                mock.patch(
                    "scripts.apt_failure_checks.APTInspector",
                    side_effect=lambda adapter, runner: SimpleNamespace(
                        adapter=adapter,
                        inventory=fixture.inventory,
                        review=fixture.review,
                    ),
                ),
            ):
                exercise_apt_failures(fixture.services, checks, root / "state.sqlite3")
            self.assertEqual(checks["phase"], "complete")
            self.assertEqual(
                checks["mid_command_cancellation"], "passed_during_download"
            )
            self.assertEqual(checks["retry_before_inspection"], "rejected")
            self.assertEqual(checks["post_cancel_inventory"], "matches_before")
            self.assertEqual(checks["fresh_retry_install_remove"], "passed")
            self.assertEqual(
                checks["zero_exit_verification_failure"],
                "passed_controlled_native_removal",
            )
            self.assertEqual(checks["false_install_success_event"], "rejected")
            self.assertFalse(fixture.installed)
            self.assertEqual(fixture.services.events.subscriber_count, 0)
            store = StateStore(root / "state.sqlite3")
            try:
                records = store.load_lifecycle()
                cancelled = next(
                    row for row in records if row.state.value == "cancelled"
                )
                self.assertIsNotNone(cancelled.cancel_requested_at)
                self.assertIsNotNone(cancelled.reconciled_at)
                failed = next(row for row in records if row.state.value == "failed")
                self.assertEqual(failed.exit_code, 0)
                self.assertEqual(failed.verification, "failed")
            finally:
                store.close()

    def test_process_group_inspection_rejects_live_or_uninspectable_members(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            child = proc / "100"
            child.mkdir()
            status = child / "stat"
            status.write_text("100 (apt helper) S 42 123 123 0\n")
            self.assertFalse(no_live_group(123, proc))
            status.write_text("100 (apt helper) Z 42 123 123 0\n")
            self.assertTrue(no_live_group(123, proc))
            status.write_text("unsupported metadata")
            self.assertFalse(no_live_group(123, proc))
            self.assertFalse(no_live_group(123, proc / "missing"))

    def test_download_message_without_active_child_is_not_cancellation_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture, _ = self.fixture(root)
            checks = {}
            with (
                mock.patch(
                    "scripts.apt_failure_checks.active_apt_child", return_value=None
                ),
                self.assertRaises(ValueError),
            ):
                exercise_apt_failures(fixture.services, checks, root / "state.sqlite3")
            self.assertEqual(checks["phase"], "cancel_active_download")
            self.assertNotIn("mid_command_cancellation", checks)

    def test_late_cancellation_after_success_cannot_count_as_a_cancelled_operation(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture, original = self.fixture(root)

            def too_late(command, **kwargs):
                if command.mutates and kwargs.get("observe"):
                    kwargs["observe"](
                        OutputSnapshot("stdout", "Get:1 already completed\n")
                    )
                return original(command, **kwargs)

            fixture.runner.run.side_effect = too_late
            checks = {}
            with (
                mock.patch(
                    "scripts.apt_failure_checks.active_apt_child",
                    return_value=1000000001,
                ),
                self.assertRaises(ValueError),
            ):
                exercise_apt_failures(fixture.services, checks, root / "state.sqlite3")
            self.assertNotIn("mid_command_cancellation", checks)
