"""No signals or native package operations: fake proc files and real lifecycle DB."""

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

from models.errors import ErrorCode, OperationError
from models.planning import utc_now
from package_managers.base import AdapterError, BackendAction
from persistence.store import StateStore
from scripts.apt_dpkg_checks import TransactionAPT, exercise_apt_dpkg, pause_owned_dpkg
from services.desktop import DesktopServices
from services.records import PackageRequest
from system.commands import CommandResult, OutputSnapshot


class DPKGWitnessTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.proc = Path(directory.name)
        children = self.proc / "100/task/100"
        children.mkdir(parents=True)
        (children / "children").write_text("101")
        self.child = self.proc / "101"
        self.child.mkdir()
        (self.child / "comm").write_text("dpkg\n")
        self.fields = ["S", "100", "100", "100", *(["0"] * 15), "1234"]
        self.stat()
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.opener = stack.enter_context(
            mock.patch(
                "scripts.apt_dpkg_checks.os.pidfd_open", create=True, return_value=543
            )
        )
        self.send = stack.enter_context(
            mock.patch("scripts.apt_dpkg_checks.signal.pidfd_send_signal", create=True)
        )
        self.closed = stack.enter_context(
            mock.patch("scripts.apt_dpkg_checks.os.close")
        )
        stack.enter_context(
            mock.patch("scripts.apt_dpkg_checks.active_apt_child", return_value=100)
        )

    def stat(self):
        (self.child / "stat").write_text("101 (dpkg) " + " ".join(self.fields) + "\n")

    def test_only_pinned_direct_child_in_the_owned_group_and_session_is_paused(self):
        self.assertEqual(pause_owned_dpkg(self.proc), (100, 101))
        self.opener.assert_called_once_with(101, 0)
        self.assertEqual(self.send.call_args.args[0], 543)
        self.closed.assert_called_once_with(543)

    def test_wrong_parent_group_session_zombie_or_executable_never_gets_a_signal(self):
        original = self.fields.copy()
        for index, value in ((1, "99"), (2, "99"), (3, "99"), (0, "Z")):
            self.fields = original.copy()
            self.fields[index] = value
            self.stat()
            self.assertIsNone(pause_owned_dpkg(self.proc))
        self.fields = original
        self.stat()
        (self.child / "comm").write_text("dpkg-deb\n")
        self.assertIsNone(pause_owned_dpkg(self.proc))
        self.send.assert_not_called()
        self.opener.assert_not_called()

    def test_identity_change_after_pidfd_open_is_rejected_and_descriptor_closed(self):
        def changed(*args):
            self.fields[19] = "5678"
            self.stat()
            return 543

        self.opener.side_effect = changed
        self.assertIsNone(pause_owned_dpkg(self.proc))
        self.send.assert_not_called()
        self.closed.assert_called_once_with(543)

    def test_missing_pidfd_or_unreadable_child_fails_without_signalling(self):
        with mock.patch("scripts.apt_dpkg_checks.os.pidfd_open", None):
            self.assertIsNone(pause_owned_dpkg(self.proc))
        (self.child / "stat").unlink()
        self.assertIsNone(pause_owned_dpkg(self.proc))
        self.send.assert_not_called()


class DPKGProfileTests(unittest.TestCase):
    def test_profile_requires_explicit_approval_before_native_work_or_report_creation(
        self,
    ):
        from scripts.qualify_native import main

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "must-not-exist.json"
            with (
                mock.patch("scripts.qualify_native.CommandRunner") as runner,
                mock.patch("builtins.print"),
            ):
                result = main(
                    ["--backend", "apt", "--apt-dpkg", "--output", str(output)]
                )
            self.assertEqual(result, 2)
            self.assertFalse(output.exists())
            runner.assert_not_called()

    def fixture(self, root):
        from tests.unit.test_native_qualification import NativeQualificationTests

        f = NativeQualificationTests().apt_fixture(root, "cmake")
        base = f.services
        f.adapter = TransactionAPT(base.host, find=f.adapter.find)
        f.services = DesktopServices(
            base.catalog,
            base.host,
            (f.adapter,),
            (f,),
            f.runner,
            require_native_qualification=False,
        )
        original = f.runner.run.side_effect
        inventory = f.inventory
        f.partial = False

        def inspect(cancel=None):
            if f.partial:
                raise AdapterError(
                    ErrorCode.VERIFICATION_FAILED, "Partial DPKG installation"
                )
            return inventory(cancel)

        f.inventory = inspect

        def run(command, **kwargs):
            if command.mutates and "install" in command.argv:
                observer = kwargs.get("observe")
                if observer:
                    observer(OutputSnapshot("stdout", "Unpacking cmake (2.0) ...\n"))
                cancel = kwargs.get("cancel")
                if cancel and cancel.is_set():
                    f.partial = True
                    f.mutations.append(command)
                    now = utc_now()
                    return CommandResult(
                        command.preview(),
                        now,
                        now,
                        -15,
                        error=OperationError(ErrorCode.CANCELLED, "Fixture cancelled"),
                    )
            if Path(command.argv[0]).name == "dpkg-query" and "--" in command.argv:
                now = utc_now()
                return CommandResult(command.preview(), now, now, 0, "half-installed\n")
            return original(command, **kwargs)

        f.runner.run.side_effect = run
        return f

    def test_fixed_pty_option_is_reviewed_only_for_native_mutations(self):
        with tempfile.TemporaryDirectory() as directory:
            f = self.fixture(Path(directory))
            plan = f.services.planner.preview((PackageRequest("cmake"),))
            command = plan.prepared[0].command
            position = command.argv.index(f.adapter.find("apt-get"))
            self.assertEqual(
                command.argv[position + 1 : position + 3], ("-o", "Dpkg::Use-Pty=0")
            )
            self.assertNotIn(
                "Dpkg::Use-Pty=0",
                f.adapter.build(BackendAction.SHOW, spec=plan.plan.items[0].spec).argv,
            )

    def test_partial_cancel_is_durable_and_never_enables_reconciliation_or_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            f = self.fixture(root)
            checks = {}
            with (
                mock.patch(
                    "scripts.apt_dpkg_checks.pause_owned_dpkg",
                    return_value=(1000000001, 1000000002),
                ),
                mock.patch("scripts.apt_dpkg_checks.no_live_group", return_value=True),
            ):
                exercise_apt_dpkg(f.services, checks, root / "state.sqlite3")
            self.assertEqual(checks["phase"], "complete")
            self.assertEqual(checks["partial_inventory"], "rejected_unavailable")
            self.assertEqual(checks["retry_before_repair"], "rejected")
            self.assertEqual(checks["native_partial_status"], "half-installed")
            self.assertEqual(checks["candidate_version"], "2.0")
            self.assertEqual(len(f.mutations), 1)
            self.assertEqual(f.services.events.subscriber_count, 0)
            store = StateStore(root / "state.sqlite3")
            try:
                record = store.load_lifecycle()[0]
                self.assertTrue(record.needs_recovery)
                self.assertEqual(record.verification, "unavailable")
                self.assertIsNone(record.reconciled_at)
                self.assertIsNotNone(record.cancel_requested_at)
            finally:
                store.close()

    def test_unpack_output_without_owned_process_is_not_native_cancellation_evidence(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            f = self.fixture(root)
            with (
                mock.patch(
                    "scripts.apt_dpkg_checks.pause_owned_dpkg", return_value=None
                ),
                self.assertRaises(ValueError),
            ):
                exercise_apt_dpkg(f.services, {}, root / "state.sqlite3")
            self.assertEqual(f.services.events.subscriber_count, 0)
