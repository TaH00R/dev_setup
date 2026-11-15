"""Opt-in smoke evidence on disposable hosted CI only; never ordinary test discovery."""

import argparse
import json
import os
import platform
import re
from collections.abc import Mapping
from pathlib import Path
from threading import Event

from models.errors import ErrorCode
from models.operations import OperationState
from models.planning import OperationAction, utc_now
from models.platform import Architecture, OperatingSystem, Platform
from package_managers.base import AdapterError
from services.catalog.loader import load_catalog
from services.catalog.service import CatalogService
from services.desktop import DesktopServices
from services.records import PackageRequest, PlanConsent, ReviewedPlan
from system.platform import detect_platform
from system.runner import CommandRunner


def disposable_guard(
    backend: str,
    host: Platform,
    environment: Mapping[str, str],
    *,
    container: bool,
    root: bool,
    approved: bool,
) -> None:
    if (
        not approved
        or environment.get("GITHUB_ACTIONS") != "true"
        or environment.get("RUNNER_ENVIRONMENT") != "github-hosted"
    ):
        raise ValueError(
            "Native smoke tests require explicit approval on a disposable GitHub-hosted runner."
        )
    if backend == "apt":
        distro = host.distribution
        if (
            host.os == OperatingSystem.LINUX
            and distro
            and (distro.id, distro.version) == ("ubuntu", "24.04")
            and container
            and root
        ):
            return
    elif (
        backend == "homebrew"
        and host.os == OperatingSystem.MACOS
        and host.architecture == Architecture.ARM64
        and not root
    ):
        return
    raise ValueError("This host does not match the disposable qualification scope.")


def manager_version(backend: str, output: str) -> str:
    first = output.splitlines()[0] if output else ""
    prefix = "apt" if backend == "apt" else "Homebrew"
    match = re.fullmatch(
        re.escape(prefix) + r" ([0-9][A-Za-z0-9.+~:_-]{0,79})(?: .*)?", first
    )
    if match is None:
        raise ValueError("Native manager version could not be identified safely.")
    return match.group(1)


def exercise(
    services: DesktopServices, package_id: str, checks: dict[str, str]
) -> None:
    """Use real application plans/consent/verification, not direct native mutation calls."""
    package = services.catalog.get(package_id)
    if package is None:
        raise ValueError("The fixed smoke package is absent.")
    mappings = tuple(
        spec for spec in package.install_specs if spec.support.supports(services.host)
    )
    if len(mappings) != 1:
        raise ValueError("Smoke scope requires one exact reviewed source.")
    spec = mappings[0]
    inspector = services.inspectors[0]
    checks["source_id"] = spec.source.id
    checks["native_id"] = spec.native_id

    def preview(action: OperationAction) -> ReviewedPlan:
        plan = services.planner.preview(
            (PackageRequest(package_id, action, spec.backend, spec.source.id),)
        )
        if not plan.plan.ready:
            raise ValueError("Native plan did not pass review.")
        return plan

    def execute(action: OperationAction) -> None:
        plan = preview(action)
        report = services.operations.execute(
            plan, consent=PlanConsent(plan, True, True)
        )
        if any(op.state != OperationState.SUCCEEDED for op in report.operations):
            raise ValueError("Native operation did not verify successfully.")

    checks["phase"] = "initial_inventory"
    present = inspector.inventory().get(spec.native_id)
    checks["inventory"] = "passed"
    if present:
        # The CLI approval explicitly includes removing a pre-existing test
        # package on the disposable runner. No force/dependency bypass is used.
        checks["phase"] = "prepare_clean_package"
        execute(OperationAction.UNINSTALL)
    checks["phase"] = "consent_and_cancellation"
    plan = preview(OperationAction.INSTALL)
    candidate = plan.prepared[0].review.version if plan.prepared[0].review else None
    if (
        candidate is None
        or re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+~:_-]{0,255}", candidate) is None
    ):
        raise ValueError("Native candidate version is not bounded version metadata.")
    checks["candidate_version"] = candidate
    denied = services.operations.execute(plan)
    if any(op.state != OperationState.CANCELLED for op in denied.operations):
        raise ValueError("Missing consent was not rejected.")
    cancelled = Event()
    cancelled.set()
    stopped = services.operations.execute(
        plan, consent=PlanConsent(plan, True, True), cancel=cancelled
    )
    if (
        any(op.state != OperationState.CANCELLED for op in stopped.operations)
        or inspector.inventory().get(spec.native_id) is not None
    ):
        raise ValueError("Pre-dispatch cancellation changed native state.")
    checks["missing_consent"] = "passed"
    checks["pre_dispatch_cancellation"] = "passed"
    checks["phase"] = "install_and_verify"
    execute(OperationAction.INSTALL)
    execute(OperationAction.VERIFY)
    installed = inspector.inventory().get(spec.native_id)
    checks["installed_origin"] = (
        installed.source_id
        if installed and installed.source_id
        else "not_proven_by_inventory"
    )
    snapshot = services.refresh(Event())
    if not any(row.package_id == package_id for row in snapshot.installed):
        raise ValueError("Refreshed inventory did not contain the installed package.")
    checks["install_verify_refresh"] = "passed"
    checks["phase"] = "upgrade_review"
    upgrade = preview(OperationAction.UPGRADE)
    # Latest -> latest is a skip, never evidence of a real version upgrade.
    checks["real_version_upgrade"] = "not_tested"
    if not all(item.skip_reason for item in upgrade.plan.items):
        raise ValueError("Candidate changed during the smoke run; review a fresh run.")
    checks["phase"] = "remove_and_stale_plan"
    stale = preview(OperationAction.UNINSTALL)
    execute(OperationAction.UNINSTALL)
    if inspector.inventory().get(spec.native_id) is not None:
        raise ValueError("Removal did not verify absence.")
    rejected = services.operations.execute(
        stale, consent=PlanConsent(stale, True, True)
    )
    if any(
        op.state != OperationState.FAILED
        or op.result is None
        or op.result.error is None
        or op.result.error.code != ErrorCode.VERIFICATION_FAILED
        for op in rejected.operations
    ):
        raise ValueError("A stale native inventory plan was not rejected.")
    checks["remove_verify"] = "passed"
    checks["stale_inventory_plan"] = "passed"
    checks["native_elevation_prompt"] = "not_tested"
    checks["mid_command_cancellation"] = "not_tested"
    checks["cask_workflow"] = "not_tested"
    checks["phase"] = "complete"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Disposable CI smoke only. Approval includes install/remove and reviewed native dependency effects for the fixed test package."
    )
    parser.add_argument("--backend", choices=("apt", "homebrew"), required=True)
    parser.add_argument("--approve-native-changes", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    host = detect_platform()
    try:
        disposable_guard(
            args.backend,
            host,
            os.environ,
            container=Path("/.dockerenv").is_file(),
            root=hasattr(os, "geteuid") and os.geteuid() == 0,
            approved=args.approve_native_changes,
        )
    except ValueError:
        print(
            "Refused: native smoke requires explicit approval and the disposable hosted scope."
        )
        return 2
    checks: dict[str, str] = {"phase": "setup"}
    report: dict[str, object] = {
        "schema_version": 1,
        "status": "failed",
        "qualified": False,
        "backend": args.backend,
        "os": host.os.value,
        "architecture": host.architecture.value,
        "os_version": host.distribution.version
        if host.distribution
        else platform.mac_ver()[0],
        "recorded_at": utc_now().isoformat(),
        "checks": checks,
    }
    revision = os.environ.get("GITHUB_SHA", "")
    report["source_commit"] = (
        revision if re.fullmatch(r"[0-9a-f]{40,64}", revision) else None
    )
    try:
        # Refuse overwrite before starting any native work.
        with args.output.open("x", encoding="utf-8") as output:
            try:
                from package_managers.apt import APT
                from package_managers.homebrew import Homebrew
                from package_managers.inspection import APTInspector, HomebrewInspector

                runner = CommandRunner()
                adapter = APT(host) if args.backend == "apt" else Homebrew(host)
                inspector = (
                    APTInspector(adapter, runner)
                    if isinstance(adapter, APT)
                    else HomebrewInspector(adapter, runner)
                )
                version = runner.run(adapter.version())
                if not version.succeeded or version.output_truncated:
                    raise ValueError("Version detection failed.")
                report["manager_version"] = manager_version(
                    args.backend, version.stdout
                )
                package_id = "cmake" if args.backend == "apt" else "ripgrep"
                package = next(p for p in load_catalog() if p.id == package_id)
                services = DesktopServices(
                    CatalogService((package,)),
                    host,
                    (adapter,),
                    (inspector,),
                    runner,
                    require_native_qualification=False,
                )
                report["package_id"] = package_id
                report["catalog_revision"] = services.catalog.revision
                exercise(services, package_id, checks)
                report["status"] = "smoke_passed_qualification_pending"
            except Exception as error:
                report["error_code"] = (
                    error.error.code.value
                    if isinstance(error, AdapterError)
                    else "verification_failed"
                )
            payload = json.dumps(report, sort_keys=True, indent=2)
            if len(payload.encode("utf-8")) > 16384:
                raise ValueError("Evidence exceeded its bound.")
            output.write(payload + "\n")
    except (OSError, ValueError):
        print("Evidence could not be created; details were omitted.")
        return 1
    print("Smoke evidence recorded. Native qualification remains pending.")
    return 0 if report["status"] == "smoke_passed_qualification_pending" else 1


if __name__ == "__main__":
    raise SystemExit(main())
