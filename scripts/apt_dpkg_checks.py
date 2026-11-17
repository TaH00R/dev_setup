"""Disposable DPKG interruption fault; leaves partial state for container disposal."""

import os
import re
import signal
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
from persistence.store import StateStore
from runtimes.service import RuntimeService
from scripts.apt_failure_checks import _preview, active_apt_child, no_live_group
from services.desktop import DesktopServices
from services.lifecycle import RecoveryService
from services.records import PlanConsent
from system.commands import Command, OutputSnapshot


class TransactionAPT(APT):
    """Expose native unpack output without a PTY; no package or shell hooks."""

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
                    "Dpkg::Use-Pty=0",
                    *command.argv[position:],
                ),
            )
        return command


def _identity(proc: Path, pid: int, parent: int) -> tuple[str, ...]:
    child = proc / str(pid)
    fields = (child / "stat").read_text().rsplit(")", 1)[1].split()
    if (
        (child / "comm").read_text().strip() != "dpkg"
        or len(fields) < 20
        or fields[0] not in {"R", "S", "D"}
        or any(fields[i] != str(parent) for i in (1, 2, 3))
    ):
        raise ValueError("Not a live owned DPKG child in the native session.")
    return (str(pid), fields[1], fields[2], fields[3], fields[19])


def pause_owned_dpkg(proc: Path = Path("/proc")) -> tuple[int, int] | None:
    """Pin a witnessed direct DPKG child by pidfd before injecting SIGSTOP."""
    opener = getattr(os, "pidfd_open", None)
    send = getattr(signal, "pidfd_send_signal", None)
    if opener is None or send is None:
        return None
    parent = active_apt_child(proc)
    if parent is None:
        return None
    try:
        children = (
            (proc / str(parent) / "task" / str(parent) / "children").read_text().split()
        )
    except OSError:
        return None
    for token in children:
        if not token.isdecimal():
            continue
        try:
            child = int(token)
            before = _identity(proc, child, parent)
            descriptor = opener(child, 0)
            try:
                if _identity(proc, child, parent) != before:
                    continue
                send(descriptor, getattr(signal, "SIGSTOP", 19))
                return parent, child
            finally:
                try:
                    os.close(descriptor)
                except OSError:
                    pass  # Do not lose the cancellation witness after SIGSTOP.
        except (OSError, ValueError, IndexError):
            continue
    return None


def exercise_apt_dpkg(
    services: DesktopServices, checks: dict[str, str], state_path: Path
) -> None:
    """Caller must first pass the explicit disposable Ubuntu/root approval guard."""
    inspector = services.inspectors[0]
    if not isinstance(inspector.adapter, TransactionAPT):
        raise ValueError("DPKG faults require the fixed transaction adapter.")
    if inspector.inventory().get("cmake") is not None:
        raise ValueError("DPKG faults require CMake to be absent on a fresh container.")
    runner = services.operations.runner
    store = StateStore(state_path)
    services.operations.lifecycle_store = store
    installed_events = []
    subscription = services.events.subscribe(
        lambda delivery: (
            installed_events.append(True)
            if isinstance(delivery.event, PackageInstalled)
            else None
        )
    )
    try:
        plan = _preview(services, OperationAction.INSTALL)
        review = plan.prepared[0].review
        if (
            review is None
            or review.version is None
            or re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+~:_-]{0,255}", review.version)
            is None
        ):
            raise ValueError("The fixed native candidate version is unavailable.")
        checks.update(source_id="ubuntu-noble", native_id="cmake")
        checks["candidate_version"] = review.version
        observed: list[tuple[int, int]] = []
        cancel = Event()

        def log(operation_id: str, output: OutputSnapshot) -> None:
            if (
                not observed
                and output.stream == "stdout"
                and re.search(r"^Unpacking cmake \(", output.text, re.MULTILINE)
            ):
                witness = pause_owned_dpkg()
                if witness is not None:
                    observed.append(witness)
                    cancel.set()

        checks["phase"] = "cancel_native_dpkg_unpack"
        report = services.operations.execute(
            plan, consent=PlanConsent(plan, True, True), cancel=cancel, logs=log
        )
        if len(report.operations) != 1:
            raise ValueError("DPKG fault did not return one reviewed operation.")
        operation = report.operations[0]
        result = operation.result
        if (
            not observed
            or result is None
            or result.state != OperationState.CANCELLED
            or result.error is None
            or result.error.code != ErrorCode.CANCELLED
            or report.persistence_errors
            or installed_events
        ):
            raise ValueError("No witnessed DPKG cancellation without false success.")
        parent, _ = observed[0]
        checks["phase"] = "check_cancelled_native_scope"
        if (Path("/proc") / str(parent)).exists() or not no_live_group(parent):
            raise ValueError("The cancelled native process scope did not stop.")
        checks["dpkg_unpack_cancellation"] = "passed_controlled_sigstop_then_cancel"
        checks["owned_native_scope_cleanup"] = "passed_no_live_members"
        checks["false_install_success_event"] = "rejected"
        checks["phase"] = "read_partial_dpkg_status"
        query = inspector.adapter.find("dpkg-query")
        if query is None:
            raise ValueError("DPKG query disappeared.")
        native = runner.run(
            inspector.adapter.request(
                (query, "--show", r"--showformat=${db:Status-Status}\n", "--", "cmake"),
                BackendAction.LIST,
                environment=(("LC_ALL", "C"),),
            )
        )
        state = native.stdout.strip()
        if (
            not native.succeeded
            or native.output_truncated
            or state
            not in {
                "half-installed",
                "unpacked",
                "half-configured",
                "triggers-awaited",
                "triggers-pending",
            }
        ):
            raise ValueError(
                "Native evidence did not establish a partial package state."
            )
        checks["native_partial_status"] = state
        try:
            inspector.inventory()
        except AdapterError as error:
            if error.error.code not in {
                ErrorCode.VERIFICATION_FAILED,
                ErrorCode.COMMAND_FAILED,
            }:
                raise
        else:
            raise ValueError("Native inventory accepted a partial installation.")
        checks["phase"] = "reopen_cancelled_operation"
        store.close()
        store = StateStore(state_path)
        services.operations.lifecycle_store = store
        record = next(
            r for r in store.interrupt_active() if r.operation_id == operation.id
        )
        if (
            not record.needs_recovery
            or not record.mutation_started
            or record.cancel_requested_at is None
            or record.state.value != "cancelled"
        ):
            raise ValueError("DPKG cancellation did not survive database reopening.")
        checks["durable_cancel_after_reopen"] = "passed"
        checks["phase"] = "inspect_partial_native_state"
        recovery = RecoveryService(
            store,
            services.host,
            services.inspectors,
            RuntimeService(services.host, runner, ()),
        )
        inspected = recovery.inspect(record)
        if inspected.verification != "unavailable":
            raise ValueError("Partial inventory was presented as a verified state.")
        try:
            recovery.reconcile(inspected, confirmed=True)
        except ValueError:
            pass
        else:
            raise ValueError("Unverified partial state was acknowledged as recovered.")
        try:
            services.operations.execute(plan, consent=PlanConsent(plan, True, True))
        except AdapterError as error:
            if error.error.code != ErrorCode.VERIFICATION_FAILED:
                raise
        else:
            raise ValueError("Unresolved DPKG work allowed another mutation.")
        checks["partial_inventory"] = "rejected_unavailable"
        checks["reconciliation_without_verified_state"] = "rejected"
        checks["retry_before_repair"] = "rejected"
        checks["native_repair"] = "not_attempted_discard_container"
        checks["phase"] = "complete"
    finally:
        subscription.close()
        store.close()
