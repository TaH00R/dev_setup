"""Operation lifecycle tests never run a package manager."""

import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from test_domain_planning import example_item

from models.errors import ErrorCode, OperationError
from models.operations import Operation, OperationResult, ProgressEvent
from models.operations import OperationState as State
from models.planning import OperationAction


class OperationModelTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 11, tzinfo=timezone.utc)
        self.item = example_item()
        self.operation = Operation(
            "example-operation", self.item, created_at=self.now, updated_at=self.now
        )

    def advance(self, operation, state, **kwargs):
        return operation.advance(state, at=self.now, **kwargs)

    def test_install_lifecycle_requires_consent_and_verification(self):
        operation = self.advance(self.operation, State.RESOLVING)
        operation = self.advance(operation, State.AWAITING_CONSENT)
        with self.assertRaises(ValueError):
            self.advance(operation, State.DOWNLOADING)
        operation = self.advance(operation, State.DOWNLOADING, consent_granted=True)
        self.assertEqual(operation.consented_at, self.now)
        operation = self.advance(operation, State.INSTALLING)
        with self.assertRaises(ValueError):
            self.advance(operation, State.SUCCEEDED)
        operation = self.advance(operation, State.VERIFYING)
        result = OperationResult(
            State.SUCCEEDED, "Verified installation.", self.now, exit_code=0
        )
        operation = self.advance(operation, State.SUCCEEDED, result=result)
        self.assertEqual(operation.result, result)
        self.assertEqual(operation.item.spec.source, self.item.spec.source)
        self.assertEqual(self.operation.state, State.QUEUED)

    def test_mutations_cannot_bypass_execution(self):
        operation = self.advance(self.operation, State.RESOLVING)
        with self.assertRaises(ValueError):
            self.advance(operation, State.VERIFYING)
        with self.assertRaises(ValueError):
            self.advance(operation, State.INSTALLING, consent_granted=True)

    def test_verification_only_flow_needs_no_mutation_consent(self):
        operation = replace(
            self.operation, item=replace(self.item, action=OperationAction.VERIFY)
        )
        operation = self.advance(operation, State.RESOLVING)
        operation = self.advance(operation, State.VERIFYING)
        result = OperationResult(State.SUCCEEDED, "Tool is present.", self.now)
        self.assertEqual(
            self.advance(operation, State.SUCCEEDED, result=result).state,
            State.SUCCEEDED,
        )

    def test_failure_and_cancellation_are_terminal(self):
        failure = OperationError(
            ErrorCode.MISSING_BACKEND, "Example backend is absent."
        )
        resolving = self.advance(self.operation, State.RESOLVING)
        for state, origin, error in [
            (State.FAILED, resolving, failure),
            (State.CANCELLED, self.operation, None),
        ]:
            result = OperationResult(state, "Stopped.", self.now, error=error)
            operation = self.advance(origin, state, result=result)
            for next_state in State:
                with (
                    self.subTest(state=state, next=next_state),
                    self.assertRaises(ValueError),
                ):
                    self.advance(operation, next_state)

    def test_skip_preserves_reason_without_execution(self):
        item = replace(self.item, commands=(), skip_reason="Already installed.")
        operation = replace(self.operation, item=item)
        operation = self.advance(operation, State.RESOLVING)
        with self.assertRaises(ValueError):
            self.advance(operation, State.AWAITING_CONSENT)
        result = OperationResult(State.SKIPPED, item.skip_reason, self.now)
        self.assertEqual(
            self.advance(operation, State.SKIPPED, result=result).state, State.SKIPPED
        )

    def test_invalid_result_combinations(self):
        for kwargs in [
            dict(state=State.INSTALLING),
            dict(state=State.FAILED),
            dict(exit_code=5),
            dict(exit_code=True),
            dict(stdout="x" * 65537),
            dict(finished_at=self.now.replace(tzinfo=None)),
            dict(error=OperationError(ErrorCode.UNKNOWN, "Failure.")),
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                OperationResult(
                    **(dict(state=State.SUCCEEDED, message="Done.") | kwargs)
                )

    def test_output_hidden_from_repr_and_truncation_explicit(self):
        result = OperationResult(
            State.CANCELLED,
            "Cancelled.",
            self.now,
            stdout="output only",
            stderr="details only",
            output_truncated=True,
        )
        self.assertNotIn("output only", repr(result))
        self.assertNotIn("details only", repr(result))
        self.assertTrue(result.output_truncated)

    def test_timestamps_terminal_results_and_progress_are_validated(self):
        with self.assertRaises(ValueError):
            self.operation.advance(State.RESOLVING, at=self.now - timedelta(seconds=1))
        with self.assertRaises(ValueError):
            self.advance(self.operation, State.CANCELLED)
        with self.assertRaises(ValueError):
            replace(
                self.operation,
                result=OperationResult(State.CANCELLED, "Cancelled.", self.now),
            )
        for percent in (-1, 101, float("nan"), float("inf"), True):
            with self.subTest(percent=percent), self.assertRaises(ValueError):
                ProgressEvent(
                    "example-operation",
                    "example.tool",
                    State.INSTALLING,
                    "Installing.",
                    percent,
                    self.now,
                )
        event = ProgressEvent(
            "example-operation",
            "example.tool",
            State.INSTALLING,
            "Installing.",
            50,
            self.now,
        )
        self.assertEqual(event.percent, 50)
