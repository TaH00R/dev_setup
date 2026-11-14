"""Output-free durable lifecycle evidence and separate aggregate plan outcomes."""

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from enum import Enum

from ._validation import identifier
from .planning import aware_timestamp, utc_now


def fingerprint(value: object) -> str:
    return hashlib.sha256(repr(value).encode("utf-8")).hexdigest()


class LifecycleState(str, Enum):
    QUEUED = "queued"
    RESOLVING = "resolving"
    AWAITING_CONSENT = "awaiting_consent"
    DOWNLOADING = "downloading"
    INSTALLING = "installing"
    VERIFYING = "verifying"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    INTERRUPTED = "interrupted"

    @property
    def terminal(self) -> bool:
        return self in {
            self.SUCCEEDED,
            self.FAILED,
            self.CANCELLED,
            self.SKIPPED,
            self.INTERRUPTED,
        }


class PlanOutcome(str, Enum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    INTERRUPTED = "interrupted"
    PARTIAL_SUCCESS = "partial_success"


def aggregate(states: tuple[str, ...]) -> PlanOutcome:
    values = {LifecycleState(state) for state in states}
    if any(not state.terminal for state in values):
        return PlanOutcome.RUNNING
    if LifecycleState.SUCCEEDED in values:
        return (
            PlanOutcome.PARTIAL_SUCCESS
            if values - {LifecycleState.SUCCEEDED, LifecycleState.SKIPPED}
            else PlanOutcome.SUCCEEDED
        )
    for state in (
        LifecycleState.INTERRUPTED,
        LifecycleState.FAILED,
        LifecycleState.CANCELLED,
    ):
        if state in values:
            return PlanOutcome(state.value)
    return PlanOutcome.SKIPPED


@dataclass(frozen=True)
class LifecycleRecord:
    operation_id: str
    execution_id: str
    plan_id: str
    plan_identity: str
    family: str
    package_id: str
    native_id: str
    backend: str
    source_id: str
    host_identity: str
    action: str
    expected_version: str | None
    before_identity: str
    approved_at: datetime | None
    created_at: datetime
    updated_at: datetime
    state: LifecycleState = LifecycleState.QUEUED
    phase: LifecycleState = LifecycleState.QUEUED
    mutation_started: bool = False
    exit_code: int | None = None
    verification: str = "unknown"
    detail: str = "none"
    inspected_at: datetime | None = None
    reconciled_at: datetime | None = None
    started_at: datetime | None = None
    cancel_requested_at: datetime | None = None
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        for name in (
            "operation_id",
            "execution_id",
            "plan_id",
            "package_id",
            "backend",
            "source_id",
        ):
            value = getattr(self, name)
            identifier(value, name)
            if len(value) > 128:
                raise ValueError("Lifecycle identifier exceeds its bound.")
        if (
            not isinstance(self.native_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+@:/-]{0,255}", self.native_id)
            is None
        ):
            raise ValueError("Lifecycle native identity is invalid.")
        for value in (self.plan_identity, self.host_identity, self.before_identity):
            if (
                not isinstance(value, str)
                or re.fullmatch(r"[0-9a-f]{64}", value) is None
            ):
                raise ValueError("Lifecycle evidence must be a SHA-256 digest.")
        if self.expected_version is not None and (
            not isinstance(self.expected_version, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.expected_version) is None
        ):
            raise ValueError("Expected version evidence must be a SHA-256 digest.")
        if self.family not in {"package", "runtime"} or self.action not in {
            "install",
            "upgrade",
            "uninstall",
            "verify",
        }:
            raise ValueError("Unsupported lifecycle family/action.")
        if not isinstance(self.state, LifecycleState) or not isinstance(
            self.phase, LifecycleState
        ):
            raise ValueError("Lifecycle states must be explicit.")
        if type(self.mutation_started) is not bool:
            raise ValueError("Mutation evidence must be boolean.")
        if self.mutation_started and self.approved_at is None:
            raise ValueError("Native mutation evidence requires approval.")
        if self.phase.terminal or self.phase == LifecycleState.CANCEL_REQUESTED:
            raise ValueError("Last execution phase must be an active phase.")
        if (
            not self.state.terminal
            and self.state != LifecycleState.CANCEL_REQUESTED
            and self.state != self.phase
        ):
            raise ValueError("Active execution state and phase must agree.")
        if self.exit_code is not None and (
            type(self.exit_code) is not int or not -(2**31) <= self.exit_code < 2**32
        ):
            raise ValueError("Invalid lifecycle exit code.")
        if self.verification not in {
            "unknown",
            "verified",
            "failed",
            "matches_expected",
            "matches_before",
            "changed",
            "unavailable",
        }:
            raise ValueError("Unsupported verification evidence.")
        if self.detail not in {
            "none",
            "dependency_failed",
            "native_changes_possible",
            "verification_failed",
            "execution_not_started",
            "outcome_unknown",
            "inspection_required",
        }:
            raise ValueError("Unsupported lifecycle detail code.")
        for name in (
            "created_at",
            "updated_at",
            "approved_at",
            "inspected_at",
            "reconciled_at",
            "started_at",
            "cancel_requested_at",
            "finished_at",
        ):
            value = getattr(self, name)
            if value is not None:
                aware_timestamp(value, name)
        if self.updated_at < self.created_at:
            raise ValueError("Lifecycle time went backwards.")
        for value in (
            self.approved_at,
            self.inspected_at,
            self.reconciled_at,
            self.started_at,
            self.cancel_requested_at,
            self.finished_at,
        ):
            if value is not None and value > self.updated_at:
                raise ValueError("Lifecycle evidence cannot come from the future.")
        if (
            self.state.terminal
            and self.state != LifecycleState.INTERRUPTED
            and self.finished_at is None
        ):
            raise ValueError("Completed execution requires a finish time.")
        if (
            self.state == LifecycleState.CANCEL_REQUESTED
            and self.cancel_requested_at is None
        ):
            raise ValueError("Cancellation requests require a timestamp.")
        if self.state == LifecycleState.SUCCEEDED and (
            self.verification != "verified" or self.exit_code not in (None, 0)
        ):
            raise ValueError(
                "Lifecycle success requires native verification and a successful exit."
            )
        if self.reconciled_at is not None and (
            not (
                self.state == LifecycleState.INTERRUPTED
                or self.detail == "inspection_required"
            )
            or self.inspected_at is None
            or self.verification in {"unknown", "unavailable"}
        ):
            raise ValueError(
                "Reconciliation requires an inspected interrupted operation."
            )

    @property
    def needs_recovery(self) -> bool:
        return (
            self.state == LifecycleState.INTERRUPTED
            or self.detail == "inspection_required"
        ) and self.reconciled_at is None

    def advance(
        self,
        state: LifecycleState,
        *,
        phase: LifecycleState | None = None,
        mutation_started: bool | None = None,
        exit_code: int | None = None,
        verification: str = "unknown",
        detail: str = "none",
    ) -> "LifecycleRecord":
        # Terminal execution outcomes are immutable. Recovery adds observations only.
        if self.state.terminal:
            raise ValueError("A terminal lifecycle cannot resume execution.")
        actual = (
            self.phase if self.state == LifecycleState.CANCEL_REQUESTED else self.state
        )
        next_phases = {
            LifecycleState.QUEUED: {LifecycleState.QUEUED, LifecycleState.RESOLVING},
            LifecycleState.RESOLVING: {
                LifecycleState.AWAITING_CONSENT,
                LifecycleState.VERIFYING,
            },
            LifecycleState.AWAITING_CONSENT: {
                LifecycleState.DOWNLOADING,
                LifecycleState.INSTALLING,
            },
            LifecycleState.DOWNLOADING: {LifecycleState.INSTALLING},
            LifecycleState.INSTALLING: {LifecycleState.VERIFYING},
            LifecycleState.VERIFYING: set(),
        }
        if (
            not state.terminal
            and state != LifecycleState.CANCEL_REQUESTED
            and state not in next_phases.get(actual, set())
        ):
            raise ValueError("Illegal lifecycle phase transition.")
        if state == LifecycleState.SUCCEEDED and (
            actual != LifecycleState.VERIFYING or verification != "verified"
        ):
            raise ValueError("Success requires completed native verification.")
        if state == LifecycleState.SKIPPED and actual != LifecycleState.RESOLVING:
            raise ValueError("A skip must be resolved without native execution.")
        now = utc_now()
        requested_at = self.cancel_requested_at or (
            now if state == LifecycleState.CANCEL_REQUESTED else None
        )
        started_at = self.started_at or (
            now if state == LifecycleState.RESOLVING else None
        )
        if self.state == LifecycleState.CANCEL_REQUESTED and not state.terminal:
            # A request does not erase the actual execution phase or mean rollback.
            state = LifecycleState.CANCEL_REQUESTED
        return replace(
            self,
            state=state,
            phase=phase
            or (
                state
                if not state.terminal and state != LifecycleState.CANCEL_REQUESTED
                else self.phase
            ),
            mutation_started=self.mutation_started
            if mutation_started is None
            else mutation_started,
            exit_code=self.exit_code if exit_code is None else exit_code,
            verification=verification,
            detail=detail,
            updated_at=now,
            started_at=started_at,
            cancel_requested_at=requested_at,
            finished_at=now
            if state.terminal and state != LifecycleState.INTERRUPTED
            else None,
        )

    def to_json(self) -> str:
        data = asdict(self)
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()
        return json.dumps(data, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "LifecycleRecord":
        if not isinstance(raw, str) or len(raw) > 8192:
            raise ValueError("Lifecycle payload exceeds its bound.")
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
            raise ValueError("Lifecycle fields do not match the schema.")
        for key in (
            "created_at",
            "updated_at",
            "approved_at",
            "inspected_at",
            "reconciled_at",
            "started_at",
            "cancel_requested_at",
            "finished_at",
        ):
            if data[key] is not None:
                data[key] = datetime.fromisoformat(data[key])
        data["state"] = LifecycleState(data["state"])
        data["phase"] = LifecycleState(data["phase"])
        return cls(**data)
