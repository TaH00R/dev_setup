"""Operation snapshots and state transitions; no execution or GUI dependencies."""

import math
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum

from ._validation import enum_value, identifier, text
from .errors import OperationError
from .planning import OperationAction, PlanItem, aware_timestamp, utc_now


class OperationState(str, Enum):
    QUEUED = "queued"
    RESOLVING = "resolving"
    AWAITING_CONSENT = "awaiting_consent"
    DOWNLOADING = "downloading"
    INSTALLING = "installing"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"

    @property
    def terminal(self) -> bool:
        return self in {
            self.SUCCEEDED,
            self.FAILED,
            self.CANCELLED,
            self.SKIPPED,
        }


_TRANSITIONS = {
    OperationState.QUEUED: {OperationState.RESOLVING, OperationState.CANCELLED},
    OperationState.RESOLVING: {
        OperationState.AWAITING_CONSENT,
        OperationState.VERIFYING,
        OperationState.FAILED,
        OperationState.CANCELLED,
        OperationState.SKIPPED,
    },
    OperationState.AWAITING_CONSENT: {
        OperationState.DOWNLOADING,
        OperationState.INSTALLING,
        OperationState.FAILED,
        OperationState.CANCELLED,
    },
    OperationState.DOWNLOADING: {
        OperationState.INSTALLING,
        OperationState.FAILED,
        OperationState.CANCELLED,
    },
    OperationState.INSTALLING: {
        OperationState.VERIFYING,
        OperationState.FAILED,
        OperationState.CANCELLED,
    },
    OperationState.VERIFYING: {
        OperationState.SUCCEEDED,
        OperationState.FAILED,
        OperationState.CANCELLED,
    },
}


def validate_progress(value: float | None) -> None:
    if value is not None and (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 <= value <= 100
    ):
        raise ValueError(
            "progress must be an available percentage from 0 to 100 or None"
        )


@dataclass(frozen=True)
class OperationResult:
    state: OperationState
    message: str
    finished_at: datetime = field(default_factory=utc_now)
    exit_code: int | None = None
    stdout: str = field(default="", repr=False)
    stderr: str = field(default="", repr=False)
    output_truncated: bool = False
    error: OperationError | None = None

    def __post_init__(self) -> None:
        enum_value(self.state, OperationState, "result state")
        if not self.state.terminal:
            raise ValueError("results require a terminal state")
        text(self.message, "result message")
        aware_timestamp(self.finished_at, "result finish time")
        if self.exit_code is not None and type(self.exit_code) is not int:
            raise ValueError("exit code must be an integer or None")
        if self.state == OperationState.SUCCEEDED and self.exit_code not in (None, 0):
            raise ValueError("non-zero exit cannot be reported as success")
        if self.state == OperationState.SKIPPED and (
            self.exit_code is not None or self.stdout or self.stderr
        ):
            raise ValueError("skipped operations cannot report command execution")
        if self.error is not None and not isinstance(self.error, OperationError):
            raise ValueError("result error must be an OperationError")
        if self.state == OperationState.FAILED and self.error is None:
            raise ValueError("failure results require a structured error")
        if (
            self.state in {OperationState.SUCCEEDED, OperationState.SKIPPED}
            and self.error
        ):
            raise ValueError("success and skip results cannot carry errors")
        for output in (self.stdout, self.stderr):
            if not isinstance(output, str) or len(output) > 65536:
                raise ValueError(
                    "each output stream must be a string bounded to 65536 characters"
                )
        if type(self.output_truncated) is not bool:
            raise ValueError("output_truncated must be a boolean")


@dataclass(frozen=True)
class ProgressEvent:
    operation_id: str
    package_id: str
    state: OperationState
    message: str
    percent: float | None = None
    timestamp: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        identifier(self.operation_id, "event operation id")
        identifier(self.package_id, "event package id")
        enum_value(self.state, OperationState, "event state")
        text(self.message, "progress message")
        validate_progress(self.percent)
        aware_timestamp(self.timestamp, "event timestamp")


@dataclass(frozen=True)
class Operation:
    id: str
    item: PlanItem
    state: OperationState = OperationState.QUEUED
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    consented_at: datetime | None = None
    progress: float | None = None
    result: OperationResult | None = None

    def __post_init__(self) -> None:
        identifier(self.id, "operation id")
        if not isinstance(self.item, PlanItem):
            raise ValueError("operation requires a PlanItem")
        enum_value(self.state, OperationState, "operation state")
        aware_timestamp(self.created_at, "operation creation time")
        aware_timestamp(self.updated_at, "operation update time")
        if self.updated_at < self.created_at:
            raise ValueError("operation update precedes creation")
        for name in ("started_at", "consented_at"):
            value = getattr(self, name)
            if value is not None:
                aware_timestamp(value, name)
                if not self.created_at <= value <= self.updated_at:
                    raise ValueError(f"{name} is outside operation lifetime")
        validate_progress(self.progress)
        if self.consented_at is not None and (
            self.started_at is None or self.consented_at < self.started_at
        ):
            raise ValueError("consent cannot precede the start of resolution")
        if self.item.action == OperationAction.VERIFY and self.state in {
            OperationState.DOWNLOADING,
            OperationState.INSTALLING,
        }:
            raise ValueError("verification cannot perform installation")
        if self.item.skip_reason is not None and self.state not in {
            OperationState.QUEUED,
            OperationState.RESOLVING,
            OperationState.SKIPPED,
            OperationState.CANCELLED,
            OperationState.FAILED,
        }:
            raise ValueError("skipped plan items cannot execute commands")
        if self.result is not None and not isinstance(self.result, OperationResult):
            raise ValueError("operation result must be an OperationResult")
        if self.state.terminal != (self.result is not None):
            raise ValueError("only terminal operations require a result")
        if self.result is not None and (
            self.result.state != self.state
            or self.result.finished_at != self.updated_at
        ):
            raise ValueError("operation state and timestamp must match its result")
        if self.state == OperationState.QUEUED and (
            self.started_at or self.consented_at
        ):
            raise ValueError(
                "queued operations cannot have started or received consent"
            )
        if (
            self.state not in {OperationState.QUEUED, OperationState.CANCELLED}
            and self.started_at is None
        ):
            raise ValueError("progressed operations require a start timestamp")
        if (
            self.item.requires_consent
            and self.state
            in {
                OperationState.DOWNLOADING,
                OperationState.INSTALLING,
                OperationState.VERIFYING,
                OperationState.SUCCEEDED,
            }
            and self.consented_at is None
        ):
            raise ValueError("mutating operations must record explicit consent")

    def advance(
        self,
        state: OperationState,
        *,
        at: datetime | None = None,
        consent_granted: bool = False,
        result: OperationResult | None = None,
        progress: float | None = None,
    ) -> "Operation":
        enum_value(state, OperationState, "next operation state")
        if state not in _TRANSITIONS.get(self.state, set()):
            raise ValueError(
                f"illegal operation transition: {self.state.value} -> {state.value}"
            )
        timestamp = utc_now() if at is None else at
        aware_timestamp(timestamp, "transition time")
        if timestamp < self.updated_at:
            raise ValueError("transition time must not go backwards")
        if type(consent_granted) is not bool:
            raise ValueError("consent_granted must be a boolean")
        consented_at = self.consented_at
        if consent_granted:
            if self.state != OperationState.AWAITING_CONSENT or state not in {
                OperationState.DOWNLOADING,
                OperationState.INSTALLING,
            }:
                raise ValueError(
                    "consent must be granted when leaving the consent state for execution"
                )
            consented_at = timestamp
        if self.state == OperationState.RESOLVING and state == OperationState.VERIFYING:
            if self.item.action != OperationAction.VERIFY:
                raise ValueError("mutations cannot bypass consent and execution")
        return replace(
            self,
            state=state,
            updated_at=timestamp,
            started_at=self.started_at
            or (timestamp if state != OperationState.CANCELLED else None),
            consented_at=consented_at,
            result=result,
            progress=progress,
        )
