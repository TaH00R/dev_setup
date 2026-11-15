"""Durability, partial outcomes and read-only reconciliation with fake inspectors."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest import mock

from models.errors import ErrorCode
from models.lifecycle import LifecycleRecord, PlanOutcome, aggregate
from models.lifecycle import LifecycleState as State
from models.operations import OperationState
from models.package import Dependency
from package_managers.base import AdapterError
from persistence.store import DesktopSettings, StateError, StateStore
from runtimes.service import RuntimeService
from services.catalog.service import CatalogService
from services.lifecycle import RecoveryService
from services.planning import Planner
from services.records import PackageRequest, PlanConsent
from tests.fixtures.desktop import DesktopFixture
from tests.fixtures.lifecycle import active_run


class LifecycleTests(unittest.TestCase):
    def sqlite_commit_failure(self, state):
        connect = sqlite3.connect

        class FailedCheckpoint(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                cursor = super().execute(sql, parameters)
                if (
                    sql.startswith("INSERT INTO lifecycle")
                    and json.loads(parameters[1])["state"] == state
                ):
                    self.fail_commit = True
                return cursor

            def commit(self):
                if getattr(self, "fail_commit", False):
                    raise sqlite3.OperationalError("disk full token=checkpoint-secret")
                return super().commit()

        return mock.patch(
            "persistence.store.sqlite3.connect",
            side_effect=lambda *args, **kwargs: connect(
                *args, factory=FailedCheckpoint, **kwargs
            ),
        )

    def test_sqlite_commit_failure_before_dispatch_prevents_native_mutation(self):
        with self.sqlite_commit_failure("installing"):
            report = self.execute()
        self.assertEqual(report.operations[0].state, OperationState.FAILED)
        self.assertFalse(self.fixture.mutations)
        self.assertTrue(report.persistence_errors)
        self.assertNotIn("checkpoint-secret", repr(report))

    def test_sqlite_terminal_commit_failure_preserves_verified_native_result(self):
        with self.sqlite_commit_failure("succeeded"):
            report = self.execute()
        self.assertEqual(report.operations[0].state, OperationState.SUCCEEDED)
        self.assertEqual(report.operations[0].result.exit_code, 0)
        self.assertTrue(report.persistence_errors)
        self.assertNotIn("checkpoint-secret", repr(report))
        self.assertEqual(self.store.load_lifecycle()[0].state, State.VERIFYING)
        self.assertEqual(self.store.interrupt_active()[0].verification, "unknown")

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.fixture = DesktopFixture(self.root)
        self.path = self.root / "state.sqlite3"
        self.store = StateStore(self.path)
        self.addCleanup(self.store.close)
        self.fixture.services.operations.lifecycle_store = self.store
        self.recovery = RecoveryService(
            self.store,
            self.fixture.services.host,
            self.fixture.services.inspectors,
            RuntimeService(self.fixture.services.host, self.fixture.runner),
        )

    def execute(self):
        reviewed = self.fixture.services.planner.preview((PackageRequest("git"),))
        return self.fixture.services.operations.execute(
            reviewed, consent=PlanConsent(reviewed, True)
        )

    def interrupted(self):
        active_run(self.fixture, self.store)
        return self.store.interrupt_active()[0]

    def test_verified_result_has_plan_identity_and_output_free_evidence(self):
        report = self.execute()
        record = self.store.load_lifecycle()[0]
        self.assertEqual(record.state, State.SUCCEEDED)
        self.assertEqual(record.verification, "verified")
        self.assertEqual(record.exit_code, 0)
        self.assertEqual(record.operation_id, report.operations[0].id)
        self.assertEqual(record.execution_id, report.execution_id)
        self.assertEqual(len(record.plan_identity), 64)
        self.assertEqual(
            record.catalog_revision, self.fixture.services.catalog.revision
        )
        self.assertTrue(record.mutation_started)
        self.assertEqual(report.outcome, PlanOutcome.SUCCEEDED)
        self.assertEqual(LifecycleRecord.from_json(record.to_json()), record)
        data = json.loads(record.to_json())
        self.assertFalse(
            set(data) & {"stdout", "stderr", "environment", "argv", "content"}
        )
        self.assertNotIn(
            "safe native output",
            self.path.read_bytes().decode("utf-8", errors="ignore"),
        )
        with self.assertRaises(ValueError):
            record.advance(State.INSTALLING)
        with self.assertRaises(StateError):
            self.store.save_lifecycle(replace(record, detail="native_changes_possible"))
        with self.assertRaises(StateError):
            self.store.save_lifecycle(replace(record, catalog_revision="0" * 64))

    def test_startup_does_no_inventory_or_mutation_until_explicit_inspection(self):
        active_run(self.fixture, self.store)
        self.fixture.threads.clear()
        self.store.close()
        reopened = StateStore(self.path)
        self.addCleanup(reopened.close)
        records = reopened.interrupt_active()
        self.assertEqual(records[0].state, State.INTERRUPTED)
        self.assertEqual(records[0].phase, State.INSTALLING)
        self.assertEqual(records[0].verification, "unknown")
        self.assertTrue(records[0].needs_recovery)
        self.fixture.runner.run.assert_not_called()
        self.assertFalse(self.fixture.threads)
        self.assertEqual(reopened.interrupt_active(), records)

    def test_expected_inventory_is_an_observation_and_requires_acknowledgement(self):
        record = self.interrupted()
        with self.assertRaises(ValueError):
            self.recovery.reconcile(record, confirmed=True)
        self.fixture.install(version="2.0")
        observed = self.recovery.inspect(record)
        self.assertEqual(observed.verification, "matches_expected")
        self.assertEqual(observed.state, State.INTERRUPTED)
        self.assertTrue(observed.needs_recovery)
        with self.assertRaises(ValueError):
            self.recovery.reconcile(observed, confirmed=False)
        resolved = self.recovery.reconcile(observed, confirmed=True)
        self.assertFalse(resolved.needs_recovery)
        self.assertEqual(resolved.state, State.INTERRUPTED)
        self.assertFalse(self.store.interrupt_active())
        self.fixture.runner.run.assert_not_called()
        with self.assertRaises(ValueError):
            self.recovery.inspect(record)

    def test_unchanged_changed_missing_backend_and_cancelled_inspection(self):
        record = self.interrupted()
        before = self.recovery.inspect(record)
        self.assertEqual(before.verification, "matches_before")
        self.fixture.install(version="unexpected")
        changed = self.recovery.inspect(before)
        self.assertEqual(changed.verification, "changed")
        self.fixture.inventory_error = ErrorCode.NETWORK
        unavailable = self.recovery.inspect(changed)
        self.assertEqual(unavailable.verification, "unavailable")
        with self.assertRaises(ValueError):
            self.recovery.reconcile(unavailable, confirmed=True)
        cancel = Event()
        cancel.set()
        self.assertEqual(self.recovery.inspect(unavailable, cancel), unavailable)
        self.assertEqual(self.store.load_lifecycle()[0], unavailable)

    def test_cancel_request_is_durable_before_native_cancellation_is_confirmed(self):
        run, operation_id = active_run(self.fixture, self.store)
        cancel = Event()
        watched = run.cancellation(operation_id, cancel)
        cancel.set()
        self.assertTrue(watched.is_set())
        record = self.store.load_lifecycle()[0]
        self.assertEqual(record.state, State.CANCEL_REQUESTED)
        self.assertEqual(record.phase, State.INSTALLING)
        self.assertFalse(record.state.terminal)
        self.assertIsNotNone(record.cancel_requested_at)
        self.assertIsNotNone(record.started_at)
        run.transition(operation_id, State.CANCELLED, detail="native_changes_possible")
        self.assertEqual(self.store.load_lifecycle()[0].state, State.CANCELLED)
        self.assertTrue(self.store.load_lifecycle()[0].mutation_started)
        self.assertEqual(
            self.store.load_lifecycle()[0].cancel_requested_at,
            record.cancel_requested_at,
        )
        self.assertIsNotNone(self.store.load_lifecycle()[0].finished_at)

    def test_lifecycle_rejects_bypassed_verification_and_backward_phases(self):
        run, operation_id = active_run(self.fixture, self.store)
        record = run.records[operation_id]
        for state in (State.RESOLVING, State.SUCCEEDED, State.SKIPPED):
            with self.assertRaises(ValueError):
                record.advance(state)
        with self.assertRaises(ValueError):
            replace(record, detail="stdout=secret")
        with self.assertRaises(ValueError):
            replace(record, plan_identity="token=secret")

    def test_failure_before_checkpoint_prevents_native_mutation(self):
        original = self.store.save_lifecycle

        def fail_installing(record):
            if record.state == State.INSTALLING:
                raise OSError("private-path-token")
            original(record)

        with mock.patch.object(
            self.store, "save_lifecycle", side_effect=fail_installing
        ):
            report = self.execute()
        self.assertEqual(report.operations[0].state, OperationState.FAILED)
        self.assertTrue(report.persistence_errors)
        self.assertNotIn("private-path-token", repr(report.persistence_errors))
        self.assertFalse(self.fixture.mutations)

    def test_failure_after_native_success_preserves_actual_result(self):
        original = self.store.save_lifecycle

        def fail_final(record):
            if record.state == State.SUCCEEDED:
                raise OSError("disk-full-secret")
            original(record)

        with mock.patch.object(self.store, "save_lifecycle", side_effect=fail_final):
            report = self.execute()
        self.assertEqual(report.operations[0].state, OperationState.SUCCEEDED)
        self.assertTrue(report.persistence_errors)
        self.assertEqual(self.store.interrupt_active()[0].verification, "unknown")

    def test_exit_zero_with_failed_verification_stays_failed_with_exit_code(self):
        self.fixture.mutate = False
        report = self.execute()
        record = self.store.load_lifecycle()[0]
        self.assertEqual(report.outcome, PlanOutcome.FAILED)
        self.assertEqual(record.state, State.FAILED)
        self.assertEqual(record.exit_code, 0)
        self.assertEqual(record.verification, "failed")
        self.assertEqual(record.detail, "verification_failed")

    def test_aggregate_partial_success_is_distinct_from_item_outcomes(self):
        self.assertEqual(
            aggregate(("succeeded", "failed", "skipped")), PlanOutcome.PARTIAL_SUCCESS
        )
        self.assertEqual(
            aggregate(("succeeded", "interrupted")), PlanOutcome.PARTIAL_SUCCESS
        )
        self.assertEqual(aggregate(("cancel_requested",)), PlanOutcome.RUNNING)
        self.assertEqual(aggregate(("cancelled", "skipped")), PlanOutcome.CANCELLED)
        self.assertEqual(aggregate(("failed", "skipped")), PlanOutcome.FAILED)

    def test_failed_dependency_is_persisted_as_skip_without_running_dependent(self):
        packages = tuple(
            replace(package, dependencies=(Dependency("cmake"),))
            if package.id == "git"
            else package
            for package in self.fixture.services.catalog.packages
        )
        planner = Planner(
            CatalogService(packages),
            self.fixture.services.host,
            self.fixture.services.inspectors,
        )
        plan = planner.preview((PackageRequest("git"),))
        self.fixture.failure = ErrorCode.COMMAND_FAILED
        report = self.fixture.services.operations.execute(
            plan, consent=PlanConsent(plan, True)
        )
        records = self.store.load_lifecycle()
        self.assertEqual(
            [record.state for record in records], [State.FAILED, State.SKIPPED]
        )
        self.assertEqual(records[1].detail, "dependency_failed")
        self.assertEqual(len(self.fixture.mutations), 1)
        self.assertEqual(report.outcome, PlanOutcome.FAILED)

    def test_cancellation_after_verified_item_has_partial_plan_outcome(self):
        cancel = Event()
        plan = self.fixture.services.planner.preview(
            (PackageRequest("git"), PackageRequest("cmake"))
        )

        def observe(event):
            if event.state == OperationState.SUCCEEDED:
                cancel.set()

        report = self.fixture.services.operations.execute(
            plan, consent=PlanConsent(plan, True), cancel=cancel, observe=observe
        )
        self.assertEqual(report.outcome, PlanOutcome.PARTIAL_SUCCESS)
        self.assertEqual(
            [record.state for record in self.store.load_lifecycle()],
            [State.SUCCEEDED, State.CANCELLED],
        )
        self.assertEqual(len(self.fixture.mutations), 1)

    def test_unresolved_interruption_blocks_another_mutation_until_reconciled(self):
        record = self.interrupted()
        with self.assertRaises(AdapterError):
            self.execute()
        self.assertFalse(self.fixture.mutations)
        inspected = self.recovery.inspect(record)
        self.recovery.reconcile(inspected, confirmed=True)
        self.assertEqual(self.execute().outcome, PlanOutcome.SUCCEEDED)

    def test_v2_upgrade_keeps_saved_theme_and_audit_and_rolls_back_failed_migration(
        self,
    ):
        self.store.save_settings(DesktopSettings(theme="light", live_search=False))
        audit = self.execute().audit
        self.store.append_audit(audit)
        self.store.close()
        with closing(sqlite3.connect(self.path)) as database:
            database.execute("DROP TABLE lifecycle")
            database.execute("PRAGMA user_version=2")
        reopened = StateStore(self.path)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.load_lifecycle(), ())
        self.assertEqual(reopened.load_settings().theme, "light")
        self.assertFalse(reopened.load_settings().live_search)
        self.assertEqual(reopened.load_history(), audit)
        reopened.close()
        with closing(sqlite3.connect(self.path)) as database:
            database.execute("PRAGMA user_version=2")
        with self.assertRaises(StateError):
            StateStore(
                self.path
            )  # Existing lifecycle table makes migration fail safely.
        with closing(sqlite3.connect(self.path)) as database:
            self.assertEqual(database.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_history_clear_preserves_unresolved_recovery_until_acknowledged(self):
        run, operation_id = active_run(
            self.fixture, self.store, packages=("git", "cmake")
        )
        run.transition(operation_id, State.VERIFYING, exit_code=0)
        run.transition(
            operation_id, State.SUCCEEDED, exit_code=0, verification="verified"
        )
        record = self.store.interrupt_active()[0]
        self.store.clear_history()
        records = self.store.load_lifecycle()
        self.assertEqual(len(records), 2)
        self.assertEqual(
            aggregate(tuple(item.state.value for item in records)),
            PlanOutcome.PARTIAL_SUCCESS,
        )
        observed = self.recovery.inspect(record)
        self.recovery.reconcile(observed, confirmed=True)
        self.store.clear_history()
        self.assertFalse(self.store.load_lifecycle())

    def test_crash_during_verification_retains_known_native_exit_code(self):
        run, operation_id = active_run(self.fixture, self.store)
        run.transition(operation_id, State.VERIFYING, exit_code=0)
        record = self.store.interrupt_active()[0]
        self.assertEqual(record.phase, State.VERIFYING)
        self.assertEqual(record.exit_code, 0)
        self.assertEqual(record.verification, "unknown")
