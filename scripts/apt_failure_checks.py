"""Controlled native faults for the explicitly approved disposable APT profile."""

import os
import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Event

from models.errors import ErrorCode
from models.events import PackageInstalled
from models.operations import OperationState
from models.planning import OperationAction
from models.specs import InstallSpec
from package_managers.apt import APT
from package_managers.base import AdapterError, BackendAction
from package_managers.inspection import APTInspector
from persistence.store import StateStore
from runtimes.service import RuntimeService
from services.desktop import DesktopServices
from services.lifecycle import RecoveryService
from services.records import PackageRequest, PlanConsent, ReviewedPlan
from system.commands import Approval, Command, CommandResult, OutputSnapshot
from system.runner import CommandRunner, OutputObserver


class DownloadLimitedAPT(APT):
    """The limit is fixed test code, not a catalog hook or persistent APT edit."""

    def _build(
        self,
        action: BackendAction,
        paths: dict[str, str],
        spec: InstallSpec | None,
        query: str | None,
    ) -> Command:
        command = super()._build(action, paths, spec, query)
        if command.mutates:
            position = command.argv.index(paths["apt-get"]) + 1
            command = replace(
                command,
                argv=(
                    *command.argv[:position],
                    "-o",
                    "Acquire::http::Dl-Limit=8",
                    *command.argv[position:],
                ),
            )
        return command


def active_apt_child(proc: Path = Path("/proc")) -> int | None:
    """Witness a direct, live apt-get child, without exporting PID or command text."""
    parent = os.getpid()
    try:
        children = (
            (proc / str(parent) / "task" / str(parent) / "children").read_text().split()
        )
    except OSError:
        return None
    for token in children:
        if not token.isdigit():
            continue
        try:
            child = proc / token
            if (child / "comm").read_text().strip() != "apt-get":
                continue
            status = (child / "status").read_text()
            if re.search(
                r"^PPid:\s*" + str(parent) + r"\s*$", status, re.MULTILINE
            ) and not re.search(r"^State:\s*[ZX]", status, re.MULTILINE):
                return int(token)
        except OSError:
            continue
    return None


def no_live_group(group_id: int, proc: Path = Path("/proc")) -> bool:
    """Fail closed if any member remains live or proc inspection is unavailable."""
    try:
        for child in proc.iterdir():
            if not child.name.isdigit():
                continue
            try:
                fields = (child / "stat").read_text().rsplit(")", 1)[1].split()
                if int(fields[2]) == group_id and fields[0] not in {"Z", "X"}:
                    return False
            except (FileNotFoundError, ProcessLookupError):
                continue
            except (OSError, ValueError, IndexError):
                return False
    except OSError:
        return False
    return True


class _AfterNativeSuccess(CommandRunner):
    def __init__(self, delegate: CommandRunner, change: Callable[[], None]) -> None:
        super().__init__()
        self.delegate, self.change = delegate, change
        self.changed = False

    def run(
        self,
        command: Command,
        *,
        approval: Approval | None = None,
        cancel: Event | None = None,
        observe: OutputObserver | None = None,
    ) -> CommandResult:
        result = self.delegate.run(
            command, approval=approval, cancel=cancel, observe=observe
        )
        if command.mutates and result.succeeded and not self.changed:
            self.changed = True
            self.change()
        return result


def _preview(services: DesktopServices, action: OperationAction) -> ReviewedPlan:
    plan = services.planner.preview(
        (PackageRequest("cmake", action, "apt", "ubuntu-noble"),)
    )
    if not plan.plan.ready or len(plan.plan.items) != 1:
        raise ValueError("The fixed CMake failure-test plan did not pass review.")
    return plan


def _succeed(services: DesktopServices, action: OperationAction) -> None:
    plan = _preview(services, action)
    report = services.operations.execute(plan, consent=PlanConsent(plan, True, True))
    if (
        len(report.operations) != 1
        or report.operations[0].state != OperationState.SUCCEEDED
        or report.persistence_errors
    ):
        raise ValueError("The reviewed native recovery action did not verify.")


def exercise_apt_failures(
    services: DesktopServices,
    checks: dict[str, str],
    state_path: Path,
) -> None:
    """Cancellation/reopen/reconciliation and a real exit-zero inventory race.

    Called only after qualify_native's disposable host/approval guard. The
    approved CMake scope includes initial cleanup and controlled native removal
    before verification. No arbitrary shell, repository edit or force flag exists.
    """
    inspector = services.inspectors[0]
    package = services.catalog.get("cmake")
    if package is None or not isinstance(inspector.adapter, DownloadLimitedAPT):
        raise ValueError("Failure tests require the fixed download-limited APT scope.")
    runner = services.operations.runner
    store = StateStore(state_path)
    services.operations.lifecycle_store = store
    subscription = None
    try:
        checks["phase"] = "initial_inventory"
        if inspector.inventory().get("cmake") is not None:
            _succeed(services, OperationAction.UNINSTALL)
        plan = _preview(services, OperationAction.INSTALL)
        candidate = plan.prepared[0].review.version if plan.prepared[0].review else None
        if (
            candidate is None
            or re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+~:_-]{0,255}", candidate) is None
        ):
            raise ValueError("Failure-test candidate is not bounded version metadata.")
        checks.update(
            source_id="ubuntu-noble", native_id="cmake", candidate_version=candidate
        )
        cancel = Event()
        observed: list[int | None] = []
        installed_events: list[bool] = []
        subscription = services.events.subscribe(
            lambda delivery: (
                installed_events.append(True)
                if isinstance(delivery.event, PackageInstalled)
                else None
            )
        )

        def log(operation_id: str, output: OutputSnapshot) -> None:
            if (
                not observed
                and output.stream == "stdout"
                and re.search(r"^Get:\d+\s", output.text, re.MULTILINE)
            ):
                observed.append(active_apt_child())
                cancel.set()

        checks["phase"] = "cancel_active_download"
        report = services.operations.execute(
            plan, consent=PlanConsent(plan, True, True), cancel=cancel, logs=log
        )
        operation = report.operations[0]
        result = operation.result
        if (
            not observed
            or observed[0] is None
            or result is None
            or operation.state != OperationState.CANCELLED
            or result.error is None
            or result.error.code != ErrorCode.CANCELLED
            or report.persistence_errors
            or installed_events
        ):
            raise ValueError(
                "Cancellation was not witnessed during an active native download."
            )
        if (Path("/proc") / str(observed[0])).exists():
            raise ValueError("The cancelled owned native child was not reaped.")
        if not no_live_group(observed[0]):
            raise ValueError(
                "The cancelled native process group still has live or uninspectable members."
            )
        checks["mid_command_cancellation"] = "passed_during_download"
        checks["native_child_reaped"] = "passed"
        checks["native_group_no_live_members"] = "passed"
        checks["false_success_after_cancel"] = "rejected"
        checks["cancelled_exit_code"] = str(result.exit_code)
        store.close()
        store = StateStore(state_path)
        services.operations.lifecycle_store = store
        records = store.interrupt_active()
        record = next(
            (item for item in records if item.operation_id == operation.id), None
        )
        if (
            record is None
            or not record.mutation_started
            or record.cancel_requested_at is None
            or record.state.value != "cancelled"
            or not record.needs_recovery
        ):
            raise ValueError(
                "Cancelled native evidence did not survive database reopen."
            )
        checks["durable_cancel_after_reopen"] = "passed"
        checks["phase"] = "blocked_retry"
        retry = _preview(services, OperationAction.INSTALL)
        try:
            services.operations.execute(retry, consent=PlanConsent(retry, True, True))
        except AdapterError as error:
            if error.error.code != ErrorCode.VERIFICATION_FAILED:
                raise
        else:
            raise ValueError("Unreconciled native work did not block retry.")
        checks["retry_before_inspection"] = "rejected"
        recovery = RecoveryService(
            store,
            services.host,
            services.inspectors,
            RuntimeService(services.host, runner, ()),
        )
        checks["phase"] = "inspect_and_acknowledge"
        inspected = recovery.inspect(record)
        if inspected.verification != "matches_before":
            raise ValueError(
                "Download cancellation did not retain the prior installed state."
            )
        try:
            recovery.reconcile(inspected, confirmed=False)
        except ValueError:
            pass
        else:
            raise ValueError("Recovery accepted missing acknowledgement.")
        reconciled = recovery.reconcile(inspected, confirmed=True)
        if reconciled.needs_recovery or reconciled.state != record.state:
            raise ValueError(
                "Reconciliation changed or failed to acknowledge the native outcome."
            )
        checks["post_cancel_inventory"] = inspected.verification
        checks["explicit_reconciliation"] = "passed"
        # A fresh plan uses the normal adapter, so it cannot reuse the throttled
        # command's consent or silently alter an already approved command.
        adapter = APT(services.host, find=inspector.adapter.find)
        native = APTInspector(adapter, runner)
        normal = DesktopServices(
            services.catalog,
            services.host,
            (adapter,),
            (native,),
            runner,
            require_native_qualification=False,
        )
        normal.operations.lifecycle_store = store
        checks["phase"] = "fresh_reviewed_retry"
        _succeed(normal, OperationAction.INSTALL)
        _succeed(normal, OperationAction.UNINSTALL)
        checks["fresh_retry_install_remove"] = "passed"
        checks["phase"] = "controlled_verification_race"
        false_success: list[bool] = []
        normal_subscription = normal.events.subscribe(
            lambda delivery: (
                false_success.append(True)
                if isinstance(delivery.event, PackageInstalled)
                else None
            )
        )

        def remove_before_verification() -> None:
            # This approved test fault models a second actor removing the tool
            # after apt-get exits zero but before the application's inventory check.
            removal = _preview(normal, OperationAction.UNINSTALL)
            consent = PlanConsent(removal, True, True)
            command = removal.prepared[0].command
            if command is None or not consent.confirmed:
                raise ValueError(
                    "Controlled native removal lacks its reviewed approval."
                )
            removed = runner.run(
                command, approval=Approval(command, elevation=consent.elevation)
            )
            if not removed.succeeded or native.inventory().get("cmake") is not None:
                raise ValueError("Controlled native state change failed.")

        faulty = _AfterNativeSuccess(runner, remove_before_verification)
        normal.operations.runner = faulty
        try:
            fault_plan = _preview(normal, OperationAction.INSTALL)
            failed = normal.operations.execute(
                fault_plan, consent=PlanConsent(fault_plan, True, True)
            )
            fault_result = failed.operations[0].result
            if (
                not faulty.changed
                or fault_result is None
                or fault_result.exit_code != 0
                or fault_result.state != OperationState.FAILED
                or fault_result.error is None
                or fault_result.error.code != ErrorCode.VERIFICATION_FAILED
                or false_success
                or failed.persistence_errors
            ):
                raise ValueError(
                    "Native exit-zero verification failure was not reported accurately."
                )
            checks["zero_exit_verification_failure"] = (
                "passed_controlled_native_removal"
            )
            checks["false_install_success_event"] = "rejected"
        finally:
            normal.operations.runner = runner
            normal_subscription.close()
        checks["native_elevation_prompt"] = "not_tested"
        checks["configured_origin_tampering"] = "not_tested"
        checks["cask_workflow"] = "not_tested"
        checks["phase"] = "complete"
    finally:
        if subscription is not None:
            subscription.close()
        store.close()
