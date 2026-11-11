"""Blocking worker service: exact consent, fresh review, execution, verification.

UI callers must run this service off the Qt thread. No passwords or rollback.
"""

from collections.abc import Callable
from threading import Event
from uuid import uuid4

from models.errors import ErrorCode, OperationError
from models.inventory import InstalledPackage
from models.operations import Operation, OperationResult, OperationState, ProgressEvent
from models.planning import OperationAction, utc_now
from package_managers.base import AdapterError
from system.commands import Approval, CommandResult, OutputSnapshot
from system.runner import CommandRunner

from .inventory import Inspector
from .planning import backend_action
from .records import (
    AuditRecord,
    ExecutionReport,
    PlanConsent,
    ReviewedPlan,
)

StateObserver = Callable[[ProgressEvent], None]
LogObserver = Callable[[str, OutputSnapshot], None]


class OperationService:
    def __init__(
        self, inspectors: tuple[Inspector, ...], runner: CommandRunner
    ) -> None:
        self.inspectors = {i.adapter.id: i for i in inspectors}
        if len(self.inspectors) != len(inspectors):
            raise ValueError("operation inspectors must be unique")
        self.runner = runner

    def execute(
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

        def emit(operation: Operation) -> None:
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
                    pass  # Observers cannot interrupt cleanup or turn success into failure.

        for item, prepared in zip(reviewed.plan.items, reviewed.prepared):
            operation = Operation(uuid4().hex, item)
            emit(operation)
            command_result: CommandResult | None = None
            dependency_failed = False

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
                                rebuilt = inspector.adapter.build(
                                    backend_action(item.action), spec=item.spec
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
                                        if logs:
                                            try:
                                                logs(operation.id, snapshot)
                                            except Exception:
                                                pass

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
            except AdapterError as error:
                finish(
                    OperationState.CANCELLED
                    if error.error.code == ErrorCode.CANCELLED
                    else OperationState.FAILED,
                    "Operation stopped; inspect the structured error. No rollback was attempted.",
                    error.error,
                )
            except Exception:
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
        return ExecutionReport(tuple(operations), tuple(audit))
