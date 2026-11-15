"""Deterministic bus contracts: no native processes or GUI imports."""

import threading
import unittest
from dataclasses import FrozenInstanceError

from models.errors import ErrorCode
from models.events import (
    EventDelivery,
    OperationChanged,
    OperationFailed,
    OperationFinished,
    PackageInstalled,
    PackageRemoved,
)
from models.operations import OperationState
from models.planning import OperationAction
from services.events import EventBus


def event(**kwargs):
    values = dict(
        correlation_id="plan-1",
        execution_id="execution-1",
        operation_id="operation-1",
        package_id="git",
        backend="winget",
        source_id="winget",
        native_id="Git.Git",
        action=OperationAction.INSTALL,
        state=OperationState.QUEUED,
    )
    values.update(kwargs)
    return OperationChanged(**values)


class ApplicationEventTests(unittest.TestCase):
    def test_event_is_immutable_and_has_no_output_or_exception_payload(self):
        value = event()
        with self.assertRaises(FrozenInstanceError):
            value.operation_id = "other"
        for field in (
            "stdout",
            "stderr",
            "message",
            "command",
            "environment",
            "traceback",
        ):
            with self.subTest(field=field), self.assertRaises(TypeError):
                event(**{field: "password=secret"})
        for changes in (
            {"operation_id": "bad/path"},
            {"native_id": "bad;command"},
            {"correlation_id": "x" * 257},
            {"error_code": "arbitrary"},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                event(**changes)

    def test_registered_event_types_reject_inconsistent_outcomes(self):
        context = vars(event(state=OperationState.SUCCEEDED))
        self.assertIsInstance(PackageInstalled(**context), OperationFinished)
        with self.assertRaises(ValueError):
            PackageRemoved(**context)
        with self.assertRaises(ValueError):
            OperationFailed(**context)
        with self.assertRaises(ValueError):
            OperationFinished(**vars(event()))
        failure = OperationFailed(
            **vars(
                event(
                    state=OperationState.FAILED,
                    error_code=ErrorCode.VERIFICATION_FAILED,
                )
            )
        )
        self.assertEqual(failure.error_code, ErrorCode.VERIFICATION_FAILED)
        with self.assertRaises(ValueError):
            EventDelivery(0, failure)

    def test_reentrant_publication_preserves_same_order_for_every_subscriber(self):
        bus, first, second = EventBus(), [], []

        def callback(delivery):
            first.append(delivery.sequence)
            if delivery.sequence == 1:
                bus.publish(event(operation_id="operation-2"))

        bus.subscribe(callback)
        bus.subscribe(lambda delivery: second.append(delivery.sequence))
        bus.publish(event())
        self.assertEqual(first, [1, 2])
        self.assertEqual(second, [1, 2])

    def test_concurrent_publishers_share_one_order(self):
        bus, first, second = EventBus(), [], []
        bus.subscribe(lambda value: first.append(value.sequence))
        bus.subscribe(lambda value: second.append(value.sequence))
        barrier = threading.Barrier(5)

        def publish():
            barrier.wait()
            for _ in range(50):
                bus.publish(event())

        threads = [threading.Thread(target=publish) for _ in range(4)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(first, list(range(1, 201)))
        self.assertEqual(second, first)

    def test_failure_removes_subscriber_and_does_not_interrupt_others(self):
        bus, received = EventBus(), []

        def broken(value):
            raise RuntimeError("password=secret")

        bus.subscribe(broken)
        bus.subscribe(received.append)
        bus.publish(event())
        bus.publish(event())
        self.assertEqual(len(received), 2)
        self.assertEqual(bus.subscriber_count, 1)
        self.assertNotIn("secret", repr(received))

    def test_cleanup_inside_delivery_and_context_manager(self):
        bus, received = EventBus(), []
        first = bus.subscribe(lambda _: second.close())
        second = bus.subscribe(received.append)
        bus.publish(event())
        self.assertEqual(received, [])
        first.close()
        first.close()
        with bus.subscribe(received.append):
            bus.publish(event())
        bus.publish(event())
        self.assertEqual(len(received), 1)
        self.assertEqual(bus.subscriber_count, 0)

    def test_late_subscription_does_not_replay_prior_operations(self):
        bus, received = EventBus(), []
        bus.publish(event())
        bus.subscribe(received.append)
        bus.publish(event())
        self.assertEqual([item.sequence for item in received], [2])

    def test_subscriber_and_reentrant_feedback_are_bounded(self):
        bus = EventBus()
        for _ in range(64):
            bus.subscribe(lambda _: None)
        with self.assertRaises(ValueError):
            bus.subscribe(lambda _: None)
        bus, received = EventBus(), []
        bus.subscribe(lambda _: bus.publish(event()))
        bus.subscribe(received.append)
        bus.publish(event())
        self.assertEqual(len(received), 1024)
        self.assertFalse(bus._pending)

    def test_nested_burst_has_a_bound_and_can_resume(self):
        bus, accepted, received = EventBus(), [], []

        def burst(value):
            if value.sequence == 1:
                for _ in range(300):
                    accepted.append(bus.publish(event()))

        bus.subscribe(burst)
        bus.subscribe(received.append)
        bus.publish(event())
        self.assertEqual(sum(accepted), 256)
        self.assertEqual(len(received), 257)
        self.assertTrue(bus.publish(event()))
        self.assertEqual(received[-1].sequence, 258)

    def test_raw_and_unregistered_payloads_are_rejected(self):
        bus = EventBus()
        with self.assertRaises(TypeError):
            bus.publish({"stdout": "secret"})

        class Unregistered(OperationChanged):
            pass

        with self.assertRaises(TypeError):
            bus.publish(Unregistered(**vars(event())))
        with self.assertRaises(ValueError):
            EventDelivery(1, {"stdout": "secret"})
