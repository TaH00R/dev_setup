"""Cross-service serialization and cancellation without native installations."""

import tempfile
import threading
import unittest
from pathlib import Path

from models.environment import RuntimeConsent, RuntimePlan, RuntimeRequest
from models.errors import ErrorCode
from package_managers.base import AdapterError
from runtimes.adapters import Uv
from runtimes.service import RuntimeService
from services.lifecycle import RecoveryService
from services.records import PackageRequest, PlanConsent
from system.coordination import CoordinationError, NativeCoordinator
from system.identity import bind
from tests.fixtures.desktop import DesktopFixture


class CoordinationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.lock = NativeCoordinator(self.root / "locks", timeout=0)

    def test_reentrant_across_objects_but_excludes_other_threads_and_cleans_up(self):
        other = NativeCoordinator(self.lock.root, timeout=0)
        errors = []

        def attempt():
            try:
                with other.hold():
                    errors.append("unexpected acquisition")
            except CoordinationError as error:
                errors.append(error.error.code)

        with self.lock.hold():
            with other.hold():
                worker = threading.Thread(target=attempt)
                worker.start()
                worker.join(5)
                self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [ErrorCode.BUSY])
        with self.assertRaises(RuntimeError):
            with other.hold():
                raise RuntimeError("fixture")
        with self.lock.hold():
            pass

    def test_cancel_while_waiting_never_enters_and_releases_local_lock(self):
        polled = threading.Event()

        class Cancel(threading.Event):
            def is_set(self):
                polled.set()
                return super().is_set()

        cancel = Cancel()
        outcomes = []

        def wait():
            try:
                with NativeCoordinator(self.lock.root, timeout=5).hold(cancel):
                    outcomes.append("entered")
            except CoordinationError as error:
                outcomes.append(error.error.code)

        with self.lock.hold():
            worker = threading.Thread(target=wait)
            worker.start()
            self.assertTrue(polled.wait(5))
            cancel.set()
            worker.join(5)
            self.assertFalse(worker.is_alive())
        self.assertEqual(outcomes, [ErrorCode.CANCELLED])
        with self.lock.hold():
            pass

    def test_native_mutation_excludes_other_service_mutations_and_inventory_reads(self):
        first_root, second_root = self.root / "first", self.root / "second"
        first_root.mkdir()
        second_root.mkdir()
        first, second = DesktopFixture(first_root), DesktopFixture(second_root)
        plans = []
        for fixture in (first, second):
            for service in (
                fixture.services.operations,
                fixture.services.planner,
                fixture.services.inventory,
            ):
                service.coordinator = NativeCoordinator(self.lock.root, timeout=0)
            plans.append(fixture.services.planner.preview((PackageRequest("git"),)))
        first.block = True
        runtime_path = self.root / "uv"
        runtime_path.write_bytes(b"Synthetic runtime manager")
        runtime_adapter = Uv(second.services.host, find=lambda _: str(runtime_path))
        runtime_service = RuntimeService(
            second.services.host, second.runner, (runtime_adapter,)
        )
        runtime_service.coordinator = NativeCoordinator(self.lock.root, timeout=0)
        request = RuntimeRequest("uv", "python", "3.13.2")
        runtime_plan = RuntimePlan(
            "runtime-plan",
            request,
            bind(runtime_adapter.install(request)),
            (),
            ("Fixture",),
        )
        results = []
        worker = threading.Thread(
            target=lambda: results.append(
                first.services.operations.execute(
                    plans[0], consent=PlanConsent(plans[0], True)
                )
            )
        )
        worker.start()
        try:
            self.assertTrue(first.entered.wait(5))
            for call in (
                lambda: second.services.operations.execute(
                    plans[1], consent=PlanConsent(plans[1], True)
                ),
                lambda: second.services.planner.preview((PackageRequest("git"),)),
                lambda: second.services.inventory.collect(),
                lambda: runtime_service.preview(request),
                lambda: runtime_service.execute(
                    runtime_plan, RuntimeConsent(runtime_plan, True)
                ),
            ):
                with self.assertRaises(AdapterError) as error:
                    call()
                self.assertEqual(error.exception.error.code, ErrorCode.BUSY)
            self.assertFalse(second.mutations)
        finally:
            first.release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0].outcome.value, "succeeded")
        self.assertEqual(
            second.services.operations.execute(
                plans[1], consent=PlanConsent(plans[1], True)
            ).outcome.value,
            "succeeded",
        )

    def test_linked_lock_directory_is_refused(self):
        target = self.root / "target"
        target.mkdir()
        link = self.root / "linked"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("Host does not permit symlink creation.")
        with self.assertRaises(CoordinationError):
            with NativeCoordinator(link).hold():
                self.fail("Linked coordination root was accepted")

    def test_timed_out_mutation_requires_inspection_and_acknowledgement_before_retry(
        self,
    ):
        fixture = DesktopFixture(self.root)
        fixture.failure = ErrorCode.TIMEOUT
        plan = fixture.services.planner.preview((PackageRequest("git"),))
        report = fixture.services.operations.execute(
            plan, consent=PlanConsent(plan, True)
        )
        record = report.lifecycle[0]
        self.assertTrue(record.needs_recovery)
        self.assertEqual(record.state.value, "failed")
        with self.assertRaises(AdapterError):
            fixture.services.operations.execute(plan, consent=PlanConsent(plan, True))
        self.assertEqual(len(fixture.mutations), 1)
        recovery = RecoveryService(
            fixture.services.operations.effective_store,
            fixture.services.host,
            fixture.services.inspectors,
            RuntimeService(fixture.services.host, fixture.runner),
        )
        observed = recovery.inspect(record)
        recovery.reconcile(observed, confirmed=True)
        fixture.failure = None
        fresh = fixture.services.planner.preview((PackageRequest("git"),))
        self.assertEqual(
            fixture.services.operations.execute(
                fresh, consent=PlanConsent(fresh, True)
            ).outcome.value,
            "succeeded",
        )

    def test_lock_timeouts_are_finite_and_bounded(self):
        for timeout in (True, float("nan"), float("inf"), -1, 61):
            with self.assertRaises(ValueError):
                NativeCoordinator(self.root / "locks", timeout=timeout)
