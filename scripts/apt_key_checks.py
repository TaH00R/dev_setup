"""Controlled signing-key drift on private manifests, key copies and cached lists."""

import re
import shutil
from pathlib import Path

from models.errors import ErrorCode
from models.operations import OperationState
from package_managers.apt import APT
from package_managers.inspection import APTInspector
from scripts.apt_origin_checks import PrivateListsRunner
from services.desktop import DesktopServices
from services.records import PackageRequest, PlanConsent


def exercise_apt_keys(
    services: DesktopServices,
    checks: dict[str, str],
    private_lists: Path,
    private_sources: Path,
    private_keys: Path,
) -> None:
    """Guarded Ubuntu image only; no native key changes or mutation dispatch."""
    base = services.inspectors[0].adapter
    if not isinstance(base, APT):
        raise ValueError("Key qualification requires APT.")
    private_keys.mkdir()
    key = private_keys / "archive.gpg"
    other = private_keys / "other.gpg"
    shutil.copyfile("/usr/share/keyrings/ubuntu-archive-keyring.gpg", key)
    shutil.copyfile(key, other)
    manifests = {}
    for source in (private_sources / "sources.list.d").glob("*.sources"):
        content = source.read_bytes()
        changed, count = re.subn(
            rb"(?m)^Signed-By:[ \t]*/usr/share/keyrings/ubuntu-archive-keyring.gpg[ \t]*$",
            lambda match: b"Signed-By: " + str(key).encode(),
            content,
        )
        if count:
            source.write_bytes(changed)
            manifests[source] = changed
    if not manifests:
        raise ValueError("The fixed Ubuntu signing-key declaration was not available.")
    runner = PrivateListsRunner(
        services.operations.runner, private_lists, private_sources
    )
    adapter = APT(services.host, find=base.find)
    inspector = APTInspector(
        adapter,
        runner,
        source_roots=(private_sources,),
        key_roots=(private_keys,),
        list_roots=(private_lists,),
    )
    native = DesktopServices(
        services.catalog,
        services.host,
        (adapter,),
        (inspector,),
        runner,
        require_native_qualification=False,
    )
    checks["phase"] = "review_private_keys"
    reviewed = native.planner.preview((PackageRequest("cmake"),))
    if (
        not reviewed.plan.ready
        or len(reviewed.prepared) != 1
        or reviewed.prepared[0].installed is not None
        or reviewed.prepared[0].review is None
        or reviewed.prepared[0].command is None
    ):
        raise ValueError("Key tests require an absent CMake and a real install plan.")
    review = reviewed.prepared[0].review
    if (
        review.version is None
        or re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+~:_-]{0,255}", review.version) is None
    ):
        raise ValueError("Key-test version is not bounded native metadata.")
    checks.update(
        source_id="ubuntu-noble", native_id="cmake", candidate_version=review.version
    )
    original_key = key.read_bytes()
    for scenario in ("key_contents", "signed_by_reference"):
        checks["phase"] = "reject_" + scenario
        try:
            if scenario == "key_contents":
                key.write_bytes(
                    b"Controlled invalid public key fixture; never installed."
                )
            else:
                for path, content in manifests.items():
                    path.write_bytes(
                        content.replace(str(key).encode(), str(other).encode())
                    )
            fresh = native.planner.preview((PackageRequest("cmake"),))
            current = fresh.prepared[0].review if fresh.plan.ready else None
            if scenario == "key_contents":
                if (
                    fresh.plan.ready
                    or not fresh.plan.issues
                    or any(
                        issue.code != ErrorCode.VERIFICATION_FAILED
                        for issue in fresh.plan.issues
                    )
                ):
                    raise ValueError(
                        "Invalid signing keys did not block fresh cache review."
                    )
                checks["key_contents_fresh_plan"] = "blocked_unverified_key"
            elif (
                current is None
                or current.version != review.version
                or current.changes != review.changes
                or current.fingerprint == review.fingerprint
            ):
                raise ValueError(
                    "The unchanged native cache did not expose trust drift in the review."
                )
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
                raise ValueError(
                    "Trust drift did not reject the stale approved plan before dispatch."
                )
            checks[scenario] = "stale_approval_rejected_before_dispatch"
        finally:
            key.write_bytes(original_key)
            for path, content in manifests.items():
                path.write_bytes(content)
        restored = native.planner.preview((PackageRequest("cmake"),))
        if not restored.plan.ready or restored.prepared[0].review != review:
            raise ValueError(
                "Restored trust inputs did not restore the reviewed identity."
            )
    checks["native_cache_candidate_effects"] = "unchanged_reference_case"
    checks["restored_trust_review"] = "passed"
    checks["native_mutation_dispatch"] = "none"
    checks["signature_authentication"] = "cached_release_checked"
    checks["phase"] = "complete"
