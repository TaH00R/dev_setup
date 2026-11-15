"""Blocking worker service: exact consent, fresh review, execution, verification.

UI callers must run this service off the Qt thread. No passwords or rollback.
"""

from collections.abc import Callable
from threading import Event
from uuid import uuid4

from models.diagnostics import DiagnosticCode, DiagnosticComponent
from models.errors import ErrorCode, OperationError
from models.inventory import InstalledPackage
from models.operations import Operation, OperationResult, OperationState, ProgressEvent
from models.planning import OperationAction, utc_now
from package_managers.base import AdapterError
from system.commands import Approval, CommandResult, OutputSnapshot
from system.coordination import NativeCoordinator
from system.diagnostics import record_diagnostic
from system.identity import IdentityError, bind
from system.runner import CommandRunner

from .coordination import access
from .events import EventBus, publish_operation
from .inventory import Inspector
from .lifecycle import LifecycleStore, MemoryLifecycleStore, package_run
from .planning import backend_action
from .records import (
    AuditRecord,
    ExecutionReport,
    PlanConsent,
    ReviewedPlan,
)
from .support import MutationPolicy

StateObserver = Callable[[ProgressEvent], None]
LogObserver = Callable[[str, OutputSnapshot], None]


class OperationService:
    def __init__(
        self,
        inspectors: tuple[Inspector, ...],
        runner: CommandRunner,
        *,
        events: EventBus | None = None,
        mutation_policy: MutationPolicy | None = None,
    ) -> None:
        self.inspectors = {i.adapter.id: i for i in inspectors}
        if len(self.inspectors) != len(inspectors):
            raise ValueError("operation inspectors must be unique")
        self.runner = runner
        self.mutation_policy = mutation_policy
        self.events = events if events is not None else EventBus()
        self.coordinator = NativeCoordinator()
        self.lifecycle_store: LifecycleStore | None = None
        self.session_store = MemoryLifecycleStore()

    @property
    def effective_store(self) -> LifecycleStore:
        return (
            self.lifecycle_store
            if self.lifecycle_store is not None
            else self.session_store
        )

    def execute(
        self,
        reviewed: ReviewedPlan,
        *,
        consent: PlanConsent | None = None,
        cancel: Event | None = None,
        observe: StateObserver | None = None,
        logs: LogObserver | None = None,
    ) -> ExecutionReport:
        if cancel is not None and cancel.is_set():
            return self._execute(
                reviewed, consent=consent, cancel=cancel, observe=observe, logs=logs
            )
        with access(self.coordinator, cancel):
            return self._execute(
                reviewed, consent=consent, cancel=cancel, observe=observe, logs=logs
            )

    def _execute(
        self,
        reviewed: ReviewedPlan,
        *,
        consent: PlanConsent | None = None,
        cancel: Event | None = None,
        observe: StateObserver | None = None,
        logs: LogObserver | None = None,
    ) -> ExecutionReport:
        if not reviewed.plan.ready:
            raise ValueError("only a ready reviewed plan can execute")
        approved = bool(consent and consent.confirmed and consent.reviewed == reviewed)
        operations: list[Operation] = []
        satisfied: set[str] = set()
        audit = []
        queued = tuple(Operation(uuid4().hex, item) for item in reviewed.plan.items)
        lifecycle = package_run(reviewed, consent, queued, self.effective_store)
        requested_cancel = cancel

        def emit(operation: Operation) -> None:
            nonlocal observe
            lifecycle.operation(
                operation,
                dependency_failed,
                exit_code=command_result.exit_code if command_result else None,
            )
            publish_operation(
                self.events,
                operation,
                reviewed.plan.id,
                lifecycle.records[operation.id].execution_id,
            )
            if observe:
                try:
                    observe(
                        ProgressEvent(
                            operation.id,
                            operation.item.package_id,
                            operation.state,
                            operation.result.message
                            if operation.result
                            else operation.state.value,
                        )
                    )
                except Exception:
                    record_diagnostic(
                        DiagnosticCode.OBSERVER_FAILED,
                        DiagnosticComponent.OPERATIONS,
                        operation_id=operation.id,
                    )
                    observe = None

        for operation, prepared in zip(queued, reviewed.prepared):
            item = operation.item
            cancel = lifecycle.cancellation(operation.id, requested_cancel)
            command_result: CommandResult | None = None
            dependency_failed = False
            emit(operation)

            def advance(
                state: OperationState, *, consent_granted: bool = False
            ) -> None:
                nonlocal operation
                operation = operation.advance(state, consent_granted=consent_granted)
                emit(operation)

            def finish(
                state: OperationState, message: str, error: OperationError | None = None
            ) -> None:
                nonlocal operation
                timestamp = utc_now()
                result = OperationResult(
                    state,
                    message,
                    timestamp,
                    command_result.exit_code if command_result else None,
                    command_result.stdout if command_result else "",
                    command_result.stderr if command_result else "",
                    command_result.output_truncated if command_result else False,
                    error,
                )
                operation = operation.advance(state, at=timestamp, result=result)
                emit(operation)

            def installed_now(inspector: Inspector) -> InstalledPackage | None:
                snapshot = inspector.inventory(cancel)
                if snapshot.backend != item.spec.backend:
                    raise AdapterError(
                        ErrorCode.VERIFICATION_FAILED,
                        "Native inventory belongs to a different backend.",
                    )
                installed = snapshot.get(item.spec.native_id)
                if (
                    installed
                    and installed.source_id is not None
                    and installed.source_id != item.spec.source.id
                ):
                    raise AdapterError(
                        ErrorCode.VERIFICATION_FAILED,
                        "Installed native source differs from the reviewed source.",
                    )
                return installed

            try:
                if cancel and cancel.is_set():
                    finish(OperationState.CANCELLED, "Cancelled before execution.")
                elif reviewed.plan.requires_consent and not approved:
                    finish(
                        OperationState.CANCELLED,
                        "Explicit approval of this exact plan was not granted.",
                    )
                else:
                    advance(OperationState.RESOLVING)
                    reason = (
                        self.mutation_policy(item.spec, item.action)
                        if self.mutation_policy
                        else None
                    )
                    if reason:
                        raise AdapterError(ErrorCode.UNSUPPORTED_OPERATION, reason)
                    inspector = self.inspectors.get(item.spec.backend)
                    if (
                        inspector is None
                        or inspector.adapter.host != reviewed.plan.platform
                    ):
                        raise AdapterError(
                            ErrorCode.UNSUPPORTED_PLATFORM,
                            "Reviewed backend/platform is unavailable to this executor.",
                        )
                    if any(dep not in satisfied for dep in item.dependencies):
                        dependency_failed = True
                        finish(
                            OperationState.SKIPPED,
                            "A required dependency did not succeed; no command ran.",
                        )
                    else:
                        current = installed_now(inspector)
                        if item.skip_reason:
                            if current != prepared.installed:
                                raise AdapterError(
                                    ErrorCode.VERIFICATION_FAILED,
                                    "Installed state changed after review; create a new plan.",
                                )
                            finish(OperationState.SKIPPED, item.skip_reason)
                        elif item.action == OperationAction.VERIFY:
                            advance(OperationState.VERIFYING)
                            expected = (
                                item.spec.version.value if item.spec.version else None
                            )
                            if current is None or (
                                expected and expected not in current.versions
                            ):
                                raise AdapterError(
                                    ErrorCode.VERIFICATION_FAILED,
                                    "Native package verification did not match the requested installation.",
                                )
                            finish(
                                OperationState.SUCCEEDED,
                                "Native package identity and requested version verified; service health is not checked.",
                            )
                        else:
                            review = prepared.review
                            command = prepared.command
                            if review is None or command is None:
                                raise AdapterError(
                                    ErrorCode.VERIFICATION_FAILED,
                                    "Prepared mutation is missing its review evidence.",
                                )
                            # Native dependencies of a preceding approved item may
                            # already have installed this package. Never reinstall.
                            if (
                                item.action == OperationAction.INSTALL
                                and current != prepared.installed
                                and current
                                and review.version in current.versions
                            ):
                                finish(
                                    OperationState.SKIPPED,
                                    "Now present at the reviewed native version; no installation command ran.",
                                )
                            else:
                                if current != prepared.installed:
                                    raise AdapterError(
                                        ErrorCode.VERIFICATION_FAILED,
                                        "Installed state changed after review; create a new plan.",
                                    )
                                fresh = inspector.review(item.spec, item.action, cancel)
                                if (
                                    fresh.version != review.version
                                    or fresh.fingerprint != review.fingerprint
                                    or not set(fresh.changes) <= set(review.changes)
                                ):
                                    raise AdapterError(
                                        ErrorCode.VERIFICATION_FAILED,
                                        "Candidate metadata or native transaction changed; new review and approval are required.",
                                    )
                                rebuilt = bind(
                                    inspector.adapter.build(
                                        backend_action(item.action), spec=item.spec
                                    )
                                )
                                if rebuilt != command:
                                    raise AdapterError(
                                        ErrorCode.VERIFICATION_FAILED,
                                        "Executable or command changed after approval.",
                                    )
                                advance(OperationState.AWAITING_CONSENT)
                                approval = Approval(
                                    command,
                                    elevation=bool(consent and consent.elevation),
                                )
                                if not approval.permits(command):
                                    raise AdapterError(
                                        ErrorCode.PERMISSION_DENIED,
                                        "The reviewed native elevation requirement was not approved.",
                                    )
                                if cancel and cancel.is_set():
                                    finish(
                                        OperationState.CANCELLED,
                                        "Cancelled before starting the native command.",
                                    )
                                else:
                                    advance(
                                        OperationState.INSTALLING, consent_granted=True
                                    )

                                    def output(snapshot: OutputSnapshot) -> None:
                                        nonlocal logs
                                        if logs:
                                            try:
                                                logs(operation.id, snapshot)
                                            except Exception:
                                                record_diagnostic(
                                                    DiagnosticCode.OBSERVER_FAILED,
                                                    DiagnosticComponent.OPERATIONS,
                                                    operation_id=operation.id,
                                                )
                                                logs = None

                                    command_result = self.runner.run(
                                        command,
                                        approval=approval,
                                        cancel=cancel,
                                        observe=output,
                                    )
                                    if not command_result.succeeded:
                                        error = command_result.error or OperationError(
                                            ErrorCode.COMMAND_FAILED,
                                            "Native command exited unsuccessfully.",
                                        )
                                        finish(
                                            OperationState.CANCELLED
                                            if error.code == ErrorCode.CANCELLED
                                            else OperationState.FAILED,
                                            "Native operation did not complete; partial system changes may remain.",
                                            error,
                                        )
                                    else:
                                        advance(OperationState.VERIFYING)
                                        installed = installed_now(inspector)
                                        if item.action == OperationAction.UNINSTALL:
                                            valid = installed is None
                                        else:
                                            valid = (
                                                installed is not None
                                                and review.version in installed.versions
                                            )
                                        if not valid:
                                            raise AdapterError(
                                                ErrorCode.VERIFICATION_FAILED,
                                                "Native command exited successfully but installed inventory did not verify its result.",
                                            )
                                        finish(
                                            OperationState.SUCCEEDED,
                                            "Native operation completed and installed inventory verified its result.",
                                        )
            except IdentityError as error:
                finish(
                    OperationState.FAILED,
                    "Launch identity changed; review and approve a fresh plan.",
                    OperationError(ErrorCode.VERIFICATION_FAILED, str(error)),
                )
            except AdapterError as error:
                finish(
                    OperationState.CANCELLED
                    if error.error.code == ErrorCode.CANCELLED
                    else OperationState.FAILED,
                    "Operation stopped; inspect the structured error. No rollback was attempted.",
                    error.error,
                )
            except Exception:
                record_diagnostic(
                    DiagnosticCode.INTERNAL_OPERATION_FAILED,
                    DiagnosticComponent.OPERATIONS,
                    error_code=ErrorCode.UNKNOWN,
                    operation_id=operation.id,
                )
                # Never put an arbitrary backend/OS exception (possibly containing
                # local configuration or credentials) in a UI message or audit record.
                finish(
                    OperationState.FAILED,
                    "Operation stopped unexpectedly; partial changes may remain.",
                    OperationError(
                        ErrorCode.UNKNOWN,
                        "An internal inspection or execution error occurred.",
                    ),
                )
            operations.append(operation)
            if operation.state == OperationState.SUCCEEDED or (
                operation.state == OperationState.SKIPPED and not dependency_failed
            ):
                satisfied.add(item.package_id)
            assert operation.result is not None
            audit.append(
                AuditRecord(
                    operation.id,
                    item.package_id,
                    item.action.value,
                    item.spec.backend,
                    item.spec.source.id,
                    prepared.review.version
                    if prepared.review
                    else item.spec.version.value
                    if item.spec.version
                    else prepared.installed.versions[0]
                    if prepared.installed and len(prepared.installed.versions) == 1
                    else None,
                    operation.state.value,
                    consent.approved_at if consent and operation.consented_at else None,
                    operation.result.finished_at,
                    operation.result.exit_code,
                )
            )
            publish_operation(
                self.events,
                operation,
                reviewed.plan.id,
                lifecycle.records[operation.id].execution_id,
                audit[-1],
            )
        return ExecutionReport(
            tuple(operations),
            tuple(audit),
            tuple(lifecycle.errors),
            next(iter(lifecycle.records.values())).execution_id
            if lifecycle.records
            else None,
            tuple(lifecycle.records.values()),
        )
