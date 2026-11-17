"""Controlled cached-release corruption; all writes stay in the private auth scope."""

import re

from models.errors import ErrorCode
from models.operations import OperationState
from package_managers.apt import APT
from package_managers.apt_trust import _read
from package_managers.base import BackendAction
from scripts.apt_auth_checks import AuthScope, _reader
from scripts.apt_origin_checks import PrivateListsRunner
from scripts.apt_tamper_checks import tamper_release
from services.desktop import DesktopServices
from services.records import PackageRequest, PlanConsent


def exercise_cached_release(
    services: DesktopServices, checks: dict[str, str], scope: AuthScope
) -> None:
    adapter = services.inspectors[0].adapter
    if not isinstance(adapter, APT):
        raise ValueError("Cached signature qualification requires APT.")
    native = _reader(services, adapter, scope)
    runner = native.operations.runner
    if not isinstance(runner, PrivateListsRunner):
        raise ValueError("The cached fixture requires a package-mutation guard.")
    checks["phase"] = "review_authenticated_cached_release"
    reviewed = native.planner.preview((PackageRequest("cmake"),))
    if (
        not reviewed.plan.ready
        or len(reviewed.prepared) != 1
        or reviewed.prepared[0].review is None
    ):
        raise ValueError("The authenticated cached release did not pass review.")
    releases = tuple(scope.lists.glob("*_dists_noble_InRelease"))
    if len(releases) != 1:
        raise ValueError("One cached noble release is required.")
    release = releases[0]
    original = _read(release, (scope.lists,))
    try:
        checks["phase"] = "reject_corrupted_cached_release"
        release.write_bytes(tamper_release(original))
        fresh = native.planner.preview((PackageRequest("cmake"),))
        if (
            fresh.plan.ready
            or not fresh.plan.issues
            or any(
                issue.code != ErrorCode.VERIFICATION_FAILED
                for issue in fresh.plan.issues
            )
        ):
            raise ValueError(
                "Corrupted cached signatures did not block fresh planning."
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
                "Stale cached approval was not rejected before package dispatch."
            )
        checks["cached_release_fresh_plan"] = "blocked_invalid_signature"
        checks["cached_release_stale_approval"] = "rejected_before_dispatch"
    finally:
        release.write_bytes(original)
    restored = native.planner.preview((PackageRequest("cmake"),))
    if (
        not restored.plan.ready
        or restored.prepared[0].review != reviewed.prepared[0].review
    ):
        raise ValueError("Restored signed bytes did not restore the original review.")
    verifier = adapter.find("gpgv")
    if verifier is None:
        raise ValueError("The cached verifier disappeared.")
    version = services.operations.runner.run(
        adapter.request(
            (verifier, "--version"),
            BackendAction.VERSION,
            environment=(("LC_ALL", "C"),),
        )
    )
    first = version.stdout.splitlines()[0] if version.stdout else ""
    match = re.fullmatch(r"gpgv \(GnuPG\) ([0-9][A-Za-z0-9.+~_-]{0,79})", first)
    if not version.succeeded or version.output_truncated or match is None:
        raise ValueError("The verifier version could not be recorded safely.")
    checks["cached_release_verifier_version"] = match[1]
    checks["cached_release_restore"] = "passed_same_review"
    checks["cached_release_refresh"] = "none"
    exercise_cached_index(services, checks, scope)


def tamper_index(data: bytes) -> bytes:
    """Alter one harmless description byte without changing package identities."""
    if len(data) > 32 * 1024 * 1024:
        raise ValueError("The controlled package-index fixture exceeds its bound.")
    package = b"Package: cmake\n"
    start = 0 if data.startswith(package) else data.find(b"\n" + package) + 1
    if not data[start:].startswith(package):
        raise ValueError("The fixed CMake fixture stanza was not found.")
    end = data.find(b"\n\n", start)
    if end < 0:
        end = len(data)
    field = b"\nDescription: "
    position = data.find(field, start, end)
    if position < 0 or position + len(field) >= end:
        raise ValueError("The fixed package description was not found.")
    position += len(field)
    if not 32 <= data[position] < 127:
        raise ValueError("The package description has no safe byte to alter.")
    replacement = b"X" if data[position : position + 1] != b"X" else b"Y"
    return data[:position] + replacement + data[position + 1 :]


def exercise_cached_index(
    services: DesktopServices, checks: dict[str, str], scope: AuthScope
) -> None:
    adapter = services.inspectors[0].adapter
    if not isinstance(adapter, APT):
        raise ValueError("Cached index qualification requires APT.")
    native = _reader(services, adapter, scope)
    runner = native.operations.runner
    if not isinstance(runner, PrivateListsRunner):
        raise ValueError("The index fixture requires a package-mutation guard.")
    checks["phase"] = "review_authenticated_cached_index"
    reviewed = native.planner.preview((PackageRequest("cmake"),))
    if not reviewed.plan.ready or len(reviewed.prepared) != 1:
        raise ValueError("The authenticated package index did not pass review.")
    indexes = tuple(scope.lists.glob("*_binary-amd64_Packages"))
    if (
        len(indexes) != 1
        or indexes[0].is_symlink()
        or not indexes[0].is_file()
        or indexes[0].stat().st_size > 32 * 1024 * 1024
    ):
        raise ValueError("One bounded uncompressed native fixture index is required.")
    index = indexes[0]
    original = index.read_bytes()
    try:
        checks["phase"] = "reject_corrupted_cached_index"
        index.write_bytes(tamper_index(original))
        fresh = native.planner.preview((PackageRequest("cmake"),))
        if (
            fresh.plan.ready
            or not fresh.plan.issues
            or any(
                issue.code != ErrorCode.VERIFICATION_FAILED
                for issue in fresh.plan.issues
            )
        ):
            raise ValueError("An index checksum mismatch did not block fresh planning.")
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
                "Corrupted index approval was not rejected before dispatch."
            )
    finally:
        index.write_bytes(original)
    restored = native.planner.preview((PackageRequest("cmake"),))
    if (
        not restored.plan.ready
        or restored.prepared[0].review != reviewed.prepared[0].review
    ):
        raise ValueError("Restored index bytes did not restore the original review.")
    checks["cached_package_index_checksums"] = "passed_sha256_and_decoded_size"
    checks["cached_index_corruption"] = "blocked_fresh_and_stale_plans"
    checks["cached_index_restore"] = "passed_same_review"
    checks["cached_index_refresh"] = "none"
    checks["cached_index_decoder"] = "apt-helper"
