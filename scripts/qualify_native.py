"""Opt-in smoke evidence on disposable hosts; never ordinary test discovery."""

import argparse
import json
import os
import platform
import re
import shutil
import tempfile
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from threading import Event

from models.errors import ErrorCode
from models.operations import OperationState
from models.package import PackageVersion
from models.planning import OperationAction, utc_now
from models.platform import Architecture, OperatingSystem, Platform
from package_managers.base import AdapterError, BackendAction
from package_managers.inspection import APTInspector
from services.catalog.loader import load_catalog
from services.catalog.service import CatalogService
from services.desktop import DesktopServices
from services.records import PackageRequest, PlanConsent, ReviewedPlan
from system.commands import Command
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
    hosted = (
        environment.get("GITHUB_ACTIONS") == "true"
        and environment.get("RUNNER_ENVIRONMENT") == "github-hosted"
    )
    local_container = environment.get("DEV_SETUP_DISPOSABLE_CONTAINER") == "1"
    if not approved:
        raise ValueError(
            "Native smoke tests require explicit approval on a disposable host."
        )
    if backend == "apt":
        distro = host.distribution
        if (
            host.os == OperatingSystem.LINUX
            and distro
            and (distro.id, distro.version) == ("ubuntu", "24.04")
            and container
            and root
            and host.architecture == Architecture.X86_64
            and (hosted or local_container)
        ):
            return
    elif (
        backend == "homebrew"
        and host.os == OperatingSystem.MACOS
        and host.architecture == Architecture.ARM64
        and not root
        and hosted
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
    services: DesktopServices,
    package_id: str,
    checks: dict[str, str],
    *,
    baseline_version: PackageVersion | None = None,
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
            (
                PackageRequest(
                    package_id,
                    action,
                    spec.backend,
                    spec.source.id,
                    baseline_version if action == OperationAction.INSTALL else None,
                ),
            )
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
    if baseline_version is None:
        # Latest -> latest is a skip, never evidence of a real version upgrade.
        checks["real_version_upgrade"] = "not_tested"
        if not all(item.skip_reason for item in upgrade.plan.items):
            raise ValueError(
                "Candidate changed during the smoke run; review a fresh run."
            )
    else:
        target = (
            upgrade.prepared[0].review.version if upgrade.prepared[0].review else None
        )
        if (
            target is None
            or target == baseline_version.value
            or any(item.skip_reason for item in upgrade.plan.items)
        ):
            raise ValueError("A real version upgrade is unavailable.")
        if re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+~:_-]{0,255}", target) is None:
            raise ValueError("Upgrade target is not bounded version metadata.")
        checks["phase"] = "real_version_upgrade"
        execute(OperationAction.UPGRADE)
        changed = inspector.inventory().get(spec.native_id)
        refreshed = services.refresh(Event())
        if (
            changed is None
            or target not in changed.versions
            or baseline_version.value in changed.versions
            or not any(
                row.package_id == package_id and target in row.versions
                for row in refreshed.installed
            )
        ):
            raise ValueError("Upgraded inventory did not verify the version change.")
        checks["upgrade_from"] = baseline_version.value
        checks["upgrade_to"] = target
        checks["real_version_upgrade"] = "passed"
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


def apt_upgrade_baseline(
    inspector: APTInspector, services: DesktopServices
) -> PackageVersion:
    """Select an older available official Git version using native Debian ordering."""
    package = services.catalog.get("git")
    if package is None:
        raise ValueError("Reviewed Git mapping is absent.")
    mappings = [
        s
        for s in package.install_specs
        if s.backend == "apt" and s.support.supports(services.host)
    ]
    if len(mappings) != 1:
        raise ValueError("APT upgrade requires one exact reviewed mapping.")
    spec = mappings[0]
    current = inspector.review(spec, OperationAction.INSTALL).version
    if current is None:
        raise ValueError("Current native candidate is unknown.")
    paths = dict(inspector.adapter.availability().executables)
    output = inspector.read(
        inspector.adapter.request(
            (paths["apt-cache"], "madison", "--", spec.native_id),
            BackendAction.SHOW,
            spec,
            environment=(("LC_ALL", "C"),),
        ),
        None,
    )
    dpkg = shutil.which("dpkg")
    if dpkg is None or not Path(dpkg).is_absolute():
        raise ValueError("Native Debian version comparison is unavailable.")
    for row in output.splitlines():
        fields = [field.strip() for field in row.split("|")]
        if (
            len(fields) != 3
            or fields[0] != spec.native_id
            or re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+~:_-]{0,255}", fields[1]) is None
        ):
            raise ValueError("Native version listing contains unsupported metadata.")
        version = fields[1]
        compared = inspector.runner.run(
            Command(
                (dpkg, "--compare-versions", version, "lt", current),
                "Compare native Debian versions for disposable qualification",
                services.host.os,
                "qualification",
            )
        )
        if (
            compared.output_truncated
            or compared.exit_code not in (0, 1)
            or compared.error
            and not (
                compared.exit_code == 1
                and compared.error.code == ErrorCode.COMMAND_FAILED
            )
        ):
            raise ValueError("Native Debian version comparison failed.")
        if compared.exit_code == 0:
            # Provenance validation is still performed by the actual inspector.
            inspector.review(
                replace(spec, version=PackageVersion(version)), OperationAction.INSTALL
            )
            return PackageVersion(version)
    raise ValueError("No older reviewed native version is currently available.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Disposable smoke only. Approval includes install/remove and reviewed native dependency effects for the fixed test package."
    )
    parser.add_argument("--backend", choices=("apt", "homebrew"), required=True)
    parser.add_argument("--approve-native-changes", action="store_true")
    profiles = parser.add_mutually_exclusive_group()
    profiles.add_argument(
        "--apt-upgrade",
        action="store_true",
        help="Test real older-to-current Git upgrade from reviewed Ubuntu repositories.",
    )
    profiles.add_argument(
        "--apt-failures",
        action="store_true",
        help="Test active-download cancellation, durable recovery and an approved native verification race.",
    )
    profiles.add_argument(
        "--apt-origins",
        action="store_true",
        help="Test altered origin rejection using a private native cache; no native mutations.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if (
        args.apt_upgrade or args.apt_failures or args.apt_origins
    ) and args.backend != "apt":
        parser.error("APT-only profiles require --backend apt")
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
            "Refused: native smoke requires explicit approval and a matching disposable scope."
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
        "profile": "apt-origins"
        if args.apt_origins
        else "apt-failures"
        if args.apt_failures
        else "apt-upgrade"
        if args.apt_upgrade
        else "smoke",
    }
    revision = os.environ.get(
        "DEV_SETUP_SOURCE_COMMIT", os.environ.get("GITHUB_SHA", "")
    )
    report["source_commit"] = (
        revision if re.fullmatch(r"[0-9a-f]{40,64}", revision) else None
    )
    tree_digest = os.environ.get("DEV_SETUP_SOURCE_DIGEST", "")
    report["source_tree_digest"] = (
        tree_digest if re.fullmatch(r"[0-9a-f]{64}", tree_digest) else None
    )
    image_digest = os.environ.get("DEV_SETUP_IMAGE_DIGEST", "")
    report["container_image_digest"] = (
        image_digest if re.fullmatch(r"sha256:[0-9a-f]{64}", image_digest) else None
    )
    try:
        # Refuse overwrite before starting any native work.
        with args.output.open("x", encoding="utf-8") as output:
            try:
                from package_managers.apt import APT
                from package_managers.homebrew import Homebrew
                from package_managers.inspection import APTInspector, HomebrewInspector
                from scripts.apt_failure_checks import (
                    DownloadLimitedAPT,
                    exercise_apt_failures,
                )

                runner = CommandRunner()
                adapter = (
                    DownloadLimitedAPT(host)
                    if args.apt_failures
                    else APT(host)
                    if args.backend == "apt"
                    else Homebrew(host)
                )
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
                package_id = (
                    "git"
                    if args.apt_upgrade
                    else "cmake"
                    if args.backend == "apt"
                    else "ripgrep"
                )
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
                baseline = (
                    apt_upgrade_baseline(inspector, services)
                    if args.apt_upgrade and isinstance(inspector, APTInspector)
                    else None
                )
                if args.apt_origins:
                    from scripts.apt_origin_checks import (
                        copy_lists,
                        exercise_apt_origins,
                    )

                    with tempfile.TemporaryDirectory(
                        prefix="dev-setup-native-origins-"
                    ) as cache_directory:
                        private_lists = Path(cache_directory) / "lists"
                        copy_lists(Path("/var/lib/apt/lists"), private_lists)
                        exercise_apt_origins(services, checks, private_lists)
                elif args.apt_failures:
                    with tempfile.TemporaryDirectory(
                        prefix="dev-setup-native-failures-"
                    ) as state_directory:
                        exercise_apt_failures(
                            services, checks, Path(state_directory) / "state.sqlite3"
                        )
                else:
                    exercise(services, package_id, checks, baseline_version=baseline)
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
