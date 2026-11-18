"""Explicit disposable-fixture repair, not an application repair facility."""

from dataclasses import replace
from pathlib import Path

from models.errors import ErrorCode
from models.lifecycle import LifecycleRecord
from models.package import PackageVersion
from models.planning import OperationAction
from models.specs import PrivilegeRequirement
from package_managers.base import AdapterError, BackendAction
from scripts.apt_failure_checks import _succeed
from services.desktop import DesktopServices
from services.lifecycle import RecoveryService
from services.records import PlanConsent, ReviewedPlan
from system.commands import Approval, Command
from system.identity import bind


def exercise_repair(
    services: DesktopServices,
    checks: dict[str, str],
    recovery: RecoveryService,
    record: LifecycleRecord,
    plan: ReviewedPlan,
) -> None:
    """Called only by the separately approved disposable DPKG repair profile."""
    if (
        not record.needs_recovery
        or record.verification != "unavailable"
        or record.state.value != "cancelled"
        or record.plan_id != plan.plan.id
        or (record.family, record.backend, record.native_id, record.source_id)
        != ("package", "apt", "cmake", "ubuntu-noble")
    ):
        raise ValueError(
            "Repair qualification requires the unresolved partial-state record."
        )
    inspector = services.inspectors[0]
    adapter = inspector.adapter
    if not plan.plan.ready or len(plan.prepared) != 1:
        raise ValueError("Repair requires one ready interrupted package plan.")
    prepared = plan.prepared[0]
    item = plan.plan.items[0]
    if (
        prepared.review is None
        or prepared.review.version is None
        or item.package_id != "cmake"
        or item.spec.backend != "apt"
        or item.spec.native_id != "cmake"
        or item.spec.source.id != "ubuntu-noble"
        or item.action != OperationAction.INSTALL
    ):
        raise ValueError("Repair is limited to the interrupted fixed CMake candidate.")
    spec = replace(item.spec, version=PackageVersion(prepared.review.version))
    dpkg = adapter.find("dpkg")
    if dpkg is None or not Path(dpkg).is_absolute():
        raise ValueError("The native DPKG executable is unavailable.")
    configure = bind(
        Command(
            (dpkg, "--configure", "--pending"),
            "Configure pending native packages in the disposable fault container",
            services.host.os,
            spec.source.id,
            mutates=True,
            privilege=PrivilegeRequirement.REQUIRED,
            timeout_seconds=180,
            environment=(("LC_ALL", "C"),),
        )
    )
    reinstall = adapter.build(BackendAction.INSTALL, spec=spec)
    position = reinstall.argv.index("install")
    reinstall = bind(
        replace(
            reinstall,
            argv=(
                *reinstall.argv[:position],
                "--reinstall",
                *reinstall.argv[position:],
            ),
        )
    )
    checks["native_repair"] = "started_explicit_fixture_repair"
    for name, command in (
        ("configure_pending", configure),
        ("reinstall_candidate", reinstall),
    ):
        checks["phase"] = "native_repair_" + name
        result = services.operations.runner.run(
            command, approval=Approval(command, elevation=True)
        )
        if not result.succeeded or result.output_truncated:
            raise ValueError(
                "The explicitly approved native fixture repair did not complete."
            )
    installed = inspector.inventory().get("cmake")
    if installed is None or prepared.review.version not in installed.versions:
        raise ValueError(
            "Native repair did not restore the reviewed version in inventory."
        )
    checks["native_repair"] = "passed_configure_pending_and_pinned_reinstall"
    checks["post_repair_inventory"] = "passed_reviewed_version"
    checks["phase"] = "reject_unacknowledged_repaired_retry"
    try:
        services.operations.execute(plan, consent=PlanConsent(plan, True, True))
    except AdapterError as error:
        if error.error.code != ErrorCode.VERIFICATION_FAILED:
            raise
    else:
        raise ValueError("Native repair silently cleared the durable recovery gate.")
    checks["retry_after_repair_before_acknowledgement"] = "rejected"
    checks["phase"] = "inspect_repaired_native_state"
    inspected = recovery.inspect(record)
    # DPKG inventory cannot prove historical repository provenance. A repaired
    # installed package is observable as changed, not as source-authenticated.
    if inspected.verification != "changed":
        raise ValueError("Repaired native state was not reported as changed.")
    try:
        recovery.reconcile(inspected, confirmed=False)
    except ValueError:
        pass
    else:
        raise ValueError(
            "Repaired-state reconciliation accepted missing acknowledgement."
        )
    reconciled = recovery.reconcile(inspected, confirmed=True)
    if reconciled.needs_recovery or reconciled.state != record.state:
        raise ValueError("Acknowledgement failed or rewrote the cancelled outcome.")
    checks["post_repair_reconciliation"] = (
        "passed_changed_state_explicit_acknowledgement"
    )
    checks["cancelled_history_preserved"] = "passed"
    checks["phase"] = "fresh_post_repair_install_remove"
    _succeed(services, OperationAction.UNINSTALL)
    _succeed(services, OperationAction.INSTALL)
    _succeed(services, OperationAction.UNINSTALL)
    checks["post_repair_fresh_install_remove"] = "passed"
