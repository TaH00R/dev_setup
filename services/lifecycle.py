"""Synchronous durable checkpoints inside workers, and read-only crash recovery."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from threading import Event, RLock
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

from models.environment import RuntimePlan, RuntimeRequest
from models.errors import ErrorCode
from models.lifecycle import (
    LifecycleRecord,
    LifecycleState,
    PlanOutcome,
    aggregate,
    fingerprint,
)
from models.operations import Operation
from models.planning import utc_now
from models.platform import Platform
from package_managers.base import AdapterError
from system.coordination import NativeCoordinator

from .coordination import access
from .inventory import Inspector
from .records import PlanConsent, ReviewedPlan

if TYPE_CHECKING:
    from runtimes.service import RuntimeService


class LifecycleStore(Protocol):
    def save_lifecycle(self, record: LifecycleRecord) -> None: ...
    def load_lifecycle(self) -> tuple[LifecycleRecord, ...]: ...


class UnavailableLifecycleStore:
    """Persistent startup must not silently turn into unjournaled native mutations."""

    def save_lifecycle(self, record: LifecycleRecord) -> None:
        raise ValueError(
            "Lifecycle storage is unavailable; no native operation was started."
        )

    def load_lifecycle(self) -> tuple[LifecycleRecord, ...]:
        raise ValueError(
            "Lifecycle storage is unavailable; no native operation was started."
        )


class MemoryLifecycleStore:
    """Session-only retry protection when the caller deliberately omits persistence."""

    def __init__(self) -> None:
        self._records: dict[str, LifecycleRecord] = {}
        self._lock = RLock()

    def save_lifecycle(self, record: LifecycleRecord) -> None:
        with self._lock:
            self._records[record.operation_id] = record

    def load_lifecycle(self) -> tuple[LifecycleRecord, ...]:
        with self._lock:
            return tuple(self._records.values())

    def clear_resolved(self) -> None:
        with self._lock:
            protected = {
                record.execution_id
                for record in self._records.values()
                if record.needs_recovery or not record.state.terminal
            }
            self._records = {
                key: record
                for key, record in self._records.items()
                if record.execution_id in protected
            }


class WatchedCancellation(Event):
    """Persist requests on the worker's next cancellation poll, never on Qt's thread."""

    def __init__(self, original: Event | None, requested: Callable[[], None]) -> None:
        super().__init__()
        self.original = original or Event()
        self.requested = requested
        self.recorded = False

    def is_set(self) -> bool:
        value = self.original.is_set()
        if value and not self.recorded:
            self.recorded = True
            self.requested()
        return value

    def set(self) -> None:
        self.original.set()

    def clear(self) -> None:
        self.original.clear()

    def wait(self, timeout: float | None = None) -> bool:
        self.original.wait(timeout)
        return self.is_set()


class LifecycleRun:
    def __init__(
        self, records: tuple[LifecycleRecord, ...], store: LifecycleStore | None
    ) -> None:
        self.records = {record.operation_id: record for record in records}
        self.store = store
        self.errors: list[str] = []
        if store is not None:
            try:
                previous_records = store.load_lifecycle()
            except Exception:
                raise AdapterError(
                    ErrorCode.UNKNOWN,
                    "Lifecycle evidence could not be read; no native operation was started.",
                ) from None
            for record in records:
                if record.action != "verify" and any(
                    (previous.needs_recovery or not previous.state.terminal)
                    and (
                        previous.family,
                        previous.backend,
                        previous.native_id,
                        previous.host_identity,
                    )
                    == (
                        record.family,
                        record.backend,
                        record.native_id,
                        record.host_identity,
                    )
                    for previous in previous_records
                ):
                    raise AdapterError(
                        ErrorCode.VERIFICATION_FAILED,
                        "Inspect and acknowledge previous unfinished work for this tool before starting a new mutation.",
                    )
        for record in records:
            if not self.save(record):
                raise AdapterError(
                    ErrorCode.UNKNOWN,
                    "Lifecycle evidence could not be saved; no native operation was started.",
                )

    @property
    def outcome(self) -> PlanOutcome:
        return aggregate(tuple(record.state.value for record in self.records.values()))

    def save(self, record: LifecycleRecord) -> bool:
        self.records[record.operation_id] = record
        if self.store is not None:
            try:
                self.store.save_lifecycle(record)
            except Exception:
                message = "Lifecycle persistence failed. The actual result remains in this session; inspect native state before any retry."
                if message not in self.errors:
                    self.errors.append(message)
                return False
        return True

    def transition(
        self,
        operation_id: str,
        state: LifecycleState,
        *,
        exit_code: int | None = None,
        verification: str = "unknown",
        detail: str = "none",
    ) -> bool:
        previous = self.records[operation_id]
        record = previous.advance(
            state,
            phase=state
            if state
            not in {LifecycleState.CANCEL_REQUESTED, LifecycleState.INTERRUPTED}
            and not state.terminal
            else previous.phase,
            mutation_started=previous.mutation_started
            or state == LifecycleState.INSTALLING,
            exit_code=exit_code,
            verification=verification,
            detail=detail,
        )
        return self.save(record)

    def cancellation(self, operation_id: str, cancel: Event | None) -> Event:
        def requested() -> None:
            if not self.records[operation_id].state.terminal:
                self.transition(
                    operation_id,
                    LifecycleState.CANCEL_REQUESTED,
                    detail="native_changes_possible"
                    if self.records[operation_id].mutation_started
                    else "execution_not_started",
                )

        return WatchedCancellation(cancel, requested)

    def operation(
        self,
        operation: Operation,
        dependency_failed: bool = False,
        *,
        exit_code: int | None = None,
    ) -> None:
        record = self.records[operation.id]
        result = operation.result
        verification = "unknown"
        detail = "none"
        if operation.state.value == "succeeded":
            verification = "verified"
        elif (
            result
            and result.error
            and result.error.code == ErrorCode.VERIFICATION_FAILED
        ):
            verification, detail = "failed", "verification_failed"
        elif dependency_failed:
            detail = "dependency_failed"
        elif result and record.mutation_started:
            detail = "native_changes_possible"
        elif result:
            detail = "execution_not_started"
        if (
            result
            and result.error
            and result.error.code in {ErrorCode.TIMEOUT, ErrorCode.CANCELLED}
            and record.mutation_started
        ):
            detail = "inspection_required"
        saved = self.transition(
            operation.id,
            LifecycleState(operation.state.value),
            exit_code=result.exit_code if result else exit_code,
            verification=verification,
            detail=detail,
        )
        if operation.state.value == "installing" and not saved:
            raise AdapterError(
                ErrorCode.UNKNOWN,
                "Lifecycle checkpoint could not be saved; the native command was not started.",
            )


def package_run(
    reviewed: ReviewedPlan,
    consent: PlanConsent | None,
    operations: tuple[Operation, ...],
    store: LifecycleStore | None,
) -> LifecycleRun:
    execution_id = uuid4().hex
    identity = fingerprint(
        (
            reviewed,
            tuple(
                (
                    item.command.argv,
                    item.command.environment,
                    item.command.launch_paths,
                    item.command.launch_files,
                )
                if item.command
                else None
                for item in reviewed.prepared
            ),
        )
    )
    approved = (
        consent.approved_at
        if consent and consent.confirmed and consent.reviewed == reviewed
        else None
    )
    records = []
    for operation, prepared in zip(operations, reviewed.prepared):
        item = operation.item
        version = (
            prepared.review.version
            if prepared.review
            else item.spec.version.value
            if item.spec.version
            else None
        )
        records.append(
            LifecycleRecord(
                operation.id,
                execution_id,
                reviewed.plan.id,
                identity,
                "package",
                item.package_id,
                item.spec.native_id,
                item.spec.backend,
                item.spec.source.id,
                fingerprint(reviewed.plan.platform),
                item.action.value,
                fingerprint(version) if version is not None else None,
                fingerprint(prepared.installed),
                approved,
                operation.created_at,
                operation.updated_at,
            )
        )
    return LifecycleRun(tuple(records), store)


def runtime_run(
    plan: RuntimePlan,
    operation_id: str,
    host: Platform,
    approved_at: datetime,
    store: LifecycleStore | None,
) -> LifecycleRun:
    now = utc_now()
    record = LifecycleRecord(
        operation_id,
        uuid4().hex,
        plan.id,
        fingerprint(
            (
                plan,
                (
                    plan.command.argv,
                    plan.command.environment,
                    plan.command.launch_paths,
                    plan.command.launch_files,
                )
                if plan.command
                else None,
            )
        ),
        "runtime",
        "runtime-" + plan.request.runtime,
        plan.request.runtime,
        plan.request.manager,
        "runtime-" + plan.request.manager,
        fingerprint(host),
        "install",
        fingerprint(plan.request.version),
        fingerprint(plan.installed),
        approved_at,
        now,
        now,
    )
    return LifecycleRun((record,), store)


class RecoveryService:
    """Inspect only known native adapters. Persisted metadata can never supply commands."""

    def __init__(
        self,
        store: LifecycleStore,
        host: Platform,
        inspectors: tuple[Inspector, ...],
        runtimes: RuntimeService,
    ) -> None:
        self.store, self.host, self.inspectors, self.runtimes = (
            store,
            host,
            inspectors,
            runtimes,
        )
        self.coordinator = NativeCoordinator()

    def inspect(
        self, record: LifecycleRecord, cancel: Event | None = None
    ) -> LifecycleRecord:
        if cancel is not None and cancel.is_set():
            return self._inspect(record, cancel)
        with access(self.coordinator, cancel):
            return self._inspect(record, cancel)

    def _inspect(
        self, record: LifecycleRecord, cancel: Event | None = None
    ) -> LifecycleRecord:
        self._current(record)
        if not record.needs_recovery:
            raise ValueError("Only unresolved interrupted work can be inspected.")
        status = "unavailable"
        if record.host_identity == fingerprint(self.host):
            try:
                if cancel and cancel.is_set():
                    return record
                if record.family == "package":
                    inspector = next(
                        (
                            item
                            for item in self.inspectors
                            if item.adapter.id == record.backend
                        ),
                        None,
                    )
                    if inspector is None:
                        raise ValueError("Backend unavailable.")
                    inventory = inspector.inventory(cancel)
                    if inventory.backend != record.backend:
                        raise ValueError("Inventory backend mismatch.")
                    package = inventory.get(record.native_id)
                    current_identity = fingerprint(package)
                    source_matches = (
                        package is None or package.source_id == record.source_id
                    )
                    expected = (
                        package is None
                        if record.action == "uninstall"
                        else bool(
                            package
                            and record.expected_version is not None
                            and any(
                                fingerprint(version) == record.expected_version
                                for version in package.versions
                            )
                            and source_matches
                        )
                    )
                else:
                    # The version itself is deliberately absent from durable metadata.
                    manager = next(
                        (
                            item
                            for item in self.runtimes.managers()
                            if item.id == record.backend
                            and record.native_id in item.runtimes
                        ),
                        None,
                    )
                    if manager is None:
                        raise ValueError("Runtime manager unavailable.")
                    versions = self.runtimes._installed(
                        manager,
                        RuntimeRequest(record.backend, record.native_id, "0.0.0"),
                        cancel,
                    )
                    current_identity = fingerprint(versions)
                    expected = any(
                        fingerprint(version) == record.expected_version
                        for version in versions
                    )
                status = (
                    "matches_expected"
                    if expected
                    else "matches_before"
                    if current_identity == record.before_identity
                    else "changed"
                )
                if cancel and cancel.is_set():
                    return record
            except Exception:
                status = "unavailable"
        observed = replace(
            record, verification=status, inspected_at=utc_now(), updated_at=utc_now()
        )
        self.store.save_lifecycle(observed)
        return observed

    def reconcile(self, record: LifecycleRecord, *, confirmed: bool) -> LifecycleRecord:
        self._current(record)
        if (
            confirmed is not True
            or not record.needs_recovery
            or record.inspected_at is None
            or record.verification in {"unknown", "unavailable"}
        ):
            raise ValueError(
                "Reconciliation requires explicit acknowledgement of an inspected outcome."
            )
        result = replace(record, reconciled_at=utc_now(), updated_at=utc_now())
        self.store.save_lifecycle(result)
        return result

    def _current(self, record: LifecycleRecord) -> None:
        current = next(
            (
                item
                for item in self.store.load_lifecycle()
                if item.operation_id == record.operation_id
            ),
            None,
        )
        if current != record:
            raise ValueError("Recovery evidence changed; reload and inspect it again.")
