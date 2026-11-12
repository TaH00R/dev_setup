"""Runtime reviews, exact consent, cancellation and native verification."""

from threading import Event
from uuid import uuid4

from models.environment import (
    RuntimeAudit,
    RuntimeConsent,
    RuntimePlan,
    RuntimeRequest,
    RuntimeResult,
)
from models.errors import ErrorCode, OperationError
from models.operations import OperationState
from models.planning import utc_now
from models.platform import Platform
from package_managers.base import AdapterError
from runtimes.adapters import RuntimeManager, default_adapters
from system.commands import Approval, CommandResult
from system.runner import CommandRunner, OutputObserver


class RuntimeService:
    def __init__(
        self,
        host: Platform,
        runner: CommandRunner,
        adapters: tuple[RuntimeManager, ...] | None = None,
    ) -> None:
        self.host, self.runner = host, runner
        self.adapters = {
            a.id: a
            for a in (adapters if adapters is not None else default_adapters(host))
        }
        if any(a.host != host for a in self.adapters.values()):
            raise ValueError("Runtime adapters must match the target host.")

    def managers(self) -> tuple[RuntimeManager, ...]:
        return tuple(self.adapters.values())

    def _adapter(self, request: RuntimeRequest) -> RuntimeManager:
        adapter = self.adapters.get(request.manager)
        if adapter is None:
            raise AdapterError(
                ErrorCode.UNSUPPORTED_OPERATION,
                "Runtime manager is staged and has no reviewed execution adapter.",
            )
        adapter.validate(request)
        return adapter

    def _installed(
        self, adapter: RuntimeManager, request: RuntimeRequest, cancel: Event | None
    ) -> tuple[str, ...]:
        result = self.runner.run(adapter.inventory(request), cancel=cancel)
        if not result.succeeded or result.output_truncated:
            raise AdapterError(
                result.error.code if result.error else ErrorCode.VERIFICATION_FAILED,
                "Native runtime inventory could not be verified.",
            )
        return adapter.parse_inventory(result.stdout)

    def preview(
        self, request: RuntimeRequest, cancel: Event | None = None
    ) -> RuntimePlan:
        adapter = self._adapter(request)
        command = adapter.install(request)
        installed = self._installed(adapter, request, cancel)
        present = request.version in installed
        return RuntimePlan(
            str(uuid4()),
            request,
            None if present else command,
            installed,
            (
                f"Source: existing {adapter.id} native manager and its runtime distribution.",
                "Installs into manager-owned storage; no PATH/shell startup edits. Rustup may set its first installed toolchain as the native default when no default exists.",
                "Review project configuration separately. Cancellation can leave downloaded runtime files; rollback is not provided.",
            ),
            "Requested version is already installed." if present else None,
        )

    def execute(
        self,
        plan: RuntimePlan,
        consent: RuntimeConsent | None = None,
        cancel: Event | None = None,
        observe: OutputObserver | None = None,
    ) -> RuntimeResult:
        if consent is None or consent.plan != plan or not consent.confirmed:
            return RuntimeResult(
                OperationState.CANCELLED, "Runtime installation was not approved."
            )
        result: CommandResult | None = None
        operation_id = str(uuid4())

        def finish(
            state: OperationState, message: str, error: OperationError | None = None
        ) -> RuntimeResult:
            audit = RuntimeAudit(
                operation_id,
                plan.id,
                plan.request.manager,
                plan.request.runtime,
                plan.request.version,
                state,
                consent.approved_at,
                utc_now(),
                result.exit_code if result else None,
            )
            return RuntimeResult(state, message, result, error, audit)

        try:
            adapter = self._adapter(plan.request)
            command = adapter.install(plan.request)
            if plan.command is not None and plan.command != command:
                raise AdapterError(
                    ErrorCode.VERIFICATION_FAILED,
                    "Runtime command changed; review a fresh plan.",
                )
            installed = self._installed(adapter, plan.request, cancel)
            if plan.request.version in installed:
                return finish(
                    OperationState.SKIPPED,
                    "Requested runtime version is already installed and verified.",
                )
            if installed != plan.installed or plan.command is None:
                raise AdapterError(
                    ErrorCode.VERIFICATION_FAILED,
                    "Runtime inventory changed; review a fresh plan.",
                )
            result = self.runner.run(
                command, approval=Approval(command), cancel=cancel, observe=observe
            )
            if not result.succeeded:
                error = result.error or OperationError(
                    ErrorCode.COMMAND_FAILED, "Native runtime installation failed."
                )
                state = (
                    OperationState.CANCELLED
                    if error.code == ErrorCode.CANCELLED
                    else OperationState.FAILED
                )
                return finish(state, error.message, error)
            if plan.request.version not in self._installed(
                adapter, plan.request, cancel
            ):
                raise AdapterError(
                    ErrorCode.VERIFICATION_FAILED,
                    "Native manager did not report the selected version after installation.",
                )
            return finish(
                OperationState.SUCCEEDED,
                "Selected runtime version was installed and verified.",
            )
        except AdapterError as exc:
            state = (
                OperationState.CANCELLED
                if exc.error.code == ErrorCode.CANCELLED
                else OperationState.FAILED
            )
            return finish(state, exc.error.message, exc.error)
        except Exception:
            error = OperationError(
                ErrorCode.UNKNOWN,
                "Runtime execution encountered an unexpected error; inspect the retained command result and review before retrying.",
            )
            return finish(OperationState.FAILED, error.message, error)
