"""Read-only native origin fault checks on a private copy of APT's list cache."""

import re
import shutil
from dataclasses import replace
from pathlib import Path
from threading import Event

from models.errors import ErrorCode
from models.operations import OperationState
from models.planning import OperationAction
from package_managers.apt import APT
from package_managers.base import BackendAction
from package_managers.inspection import APTInspector
from services.desktop import DesktopServices
from services.records import PackageRequest, PlanConsent
from system.commands import Approval, Command, CommandResult
from system.runner import CommandRunner, OutputObserver


class PrivateListsRunner(CommandRunner):
    """Redirect APT reads to private lists; refuse every mutation dispatch."""

    def __init__(self, delegate: CommandRunner, lists: Path) -> None:
        super().__init__()
        self.delegate, self.lists = delegate, lists
        self.mutation_attempts = 0

    def run(
        self,
        command: Command,
        *,
        approval: Approval | None = None,
        cancel: Event | None = None,
        observe: OutputObserver | None = None,
    ) -> CommandResult:
        if command.mutates:
            self.mutation_attempts += 1
            raise ValueError("Origin qualification never dispatches native mutations.")
        if Path(command.argv[0]).name in {"apt-cache", "apt-get"}:
            command = replace(
                command,
                argv=(
                    command.argv[0],
                    "-o",
                    f"Dir::State::lists={self.lists}",
                    "-o",
                    "Dir::Cache::pkgcache=",
                    "-o",
                    "Dir::Cache::srcpkgcache=",
                    *command.argv[1:],
                ),
            )
        return self.delegate.run(
            command, approval=approval, cancel=cancel, observe=observe
        )


def copy_lists(source: Path, target: Path) -> None:
    """Copy regular cache files only, without following links or changing originals."""
    target.mkdir()
    for path in source.iterdir():
        if path.is_symlink():
            raise ValueError("Native list cache contains an unexpected link.")
        if path.is_file():
            shutil.copyfile(path, target / path.name)


def exercise_apt_origins(
    services: DesktopServices, checks: dict[str, str], private_lists: Path
) -> None:
    """Called only after the disposable guard; no downloads or native installs."""
    base = services.inspectors[0].adapter
    if not isinstance(base, APT):
        raise ValueError("Origin qualification requires APT.")
    runner = PrivateListsRunner(services.operations.runner, private_lists)
    adapter = APT(services.host, find=base.find)
    inspector = APTInspector(adapter, runner)
    native = DesktopServices(
        services.catalog,
        services.host,
        (adapter,),
        (inspector,),
        runner,
        require_native_qualification=False,
    )
    request = (PackageRequest("cmake", OperationAction.INSTALL, "apt", "ubuntu-noble"),)
    checks["phase"] = "review_private_cache"
    reviewed = native.planner.preview(request)
    if (
        not reviewed.plan.ready
        or len(reviewed.prepared) != 1
        or reviewed.prepared[0].installed is not None
        or reviewed.prepared[0].review is None
        or reviewed.prepared[0].command is None
    ):
        raise ValueError(
            "Origin test requires an absent CMake and a real install plan."
        )
    version = reviewed.prepared[0].review.version
    if (
        version is None
        or re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+~:_-]{0,255}", version) is None
    ):
        raise ValueError("Origin-test version is not bounded native metadata.")
    checks.update(
        source_id="ubuntu-noble", native_id="cmake", candidate_version=version
    )
    originals: dict[Path, bytes] = {}
    try:
        for path in private_lists.glob("*_InRelease"):
            if not path.is_file() or path.is_symlink():
                raise ValueError("Private release cache is not a regular file.")
            content = path.read_bytes()
            if b"\nOrigin: Ubuntu\n" in content:
                originals[path] = content
                path.write_bytes(
                    content.replace(
                        b"\nOrigin: Ubuntu\n", b"\nOrigin: DevSetupUnreviewed\n"
                    )
                )
        if not originals:
            raise ValueError("No reviewed release metadata was available to alter.")
        # This deliberately corrupts only copied, already-cached release metadata.
        # APT's native cache reader must expose it; no update/signature bypass runs.
        checks["phase"] = "reject_altered_cached_origin"
        policy = inspector.read(
            adapter.request(
                (adapter.find("apt-cache") or "", "policy"),
                BackendAction.SHOW,
                reviewed.plan.items[0].spec,
                environment=(("LC_ALL", "C"),),
            ),
            None,
        )
        if "o=DevSetupUnreviewed," not in policy or re.search(
            r"(?:^|,)o=Ubuntu(?:,|$)", policy, re.MULTILINE
        ):
            raise ValueError("Native APT did not expose the altered private origins.")
        fresh = native.planner.preview(request)
        if (
            fresh.plan.ready
            or not fresh.plan.issues
            or any(
                issue.code != ErrorCode.VERIFICATION_FAILED
                for issue in fresh.plan.issues
            )
        ):
            raise ValueError("Altered origins did not block fresh planning.")
        checks["fresh_plan_altered_origin"] = "rejected"
        report = native.operations.execute(
            reviewed, consent=PlanConsent(reviewed, True, True)
        )
        result = report.operations[0].result
        if (
            len(report.operations) != 1
            or result is None
            or result.state != OperationState.FAILED
            or result.error is None
            or result.error.code != ErrorCode.VERIFICATION_FAILED
            or result.exit_code is not None
            or report.persistence_errors
            or runner.mutation_attempts
        ):
            raise ValueError("Changed origins were not rejected before dispatch.")
        checks["reviewed_plan_altered_origin"] = "rejected_before_dispatch"
    finally:
        for path, content in originals.items():
            path.write_bytes(content)
    checks["phase"] = "review_restored_private_cache"
    restored = native.planner.preview(request)
    if (
        not restored.plan.ready
        or restored.prepared[0].review != reviewed.prepared[0].review
        or runner.mutation_attempts
    ):
        raise ValueError(
            "Restored private origins did not restore the original review."
        )
    checks["restored_origin_review"] = "passed"
    checks["native_mutation_dispatch"] = "none"
    checks["cached_origin_tampering"] = "rejected_private_cache"
    checks["signature_verification_bypass"] = "none"
    checks["configured_repository_change"] = "not_tested"
    checks["phase"] = "complete"
