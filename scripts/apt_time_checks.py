"""Controlled review-clock tests against real signed metadata; no clock writes."""

from datetime import timedelta

from models.errors import ErrorCode
from models.operations import OperationState
from models.planning import utc_now
from package_managers.apt import APT
from package_managers.apt_freshness import release_times
from package_managers.apt_trust import _read
from package_managers.inspection import APTInspector
from scripts.apt_auth_checks import AuthScope, _reader
from scripts.apt_origin_checks import PrivateListsRunner
from services.desktop import DesktopServices
from services.records import PackageRequest, PlanConsent


def exercise_cached_time(
    services: DesktopServices, checks: dict[str, str], scope: AuthScope
) -> None:
    adapter = services.inspectors[0].adapter
    if not isinstance(adapter, APT):
        raise ValueError("Signed-time checks require APT.")
    native = _reader(services, adapter, scope)
    inspector = native.inspectors[0]
    runner = native.operations.runner
    if not isinstance(inspector, APTInspector) or not isinstance(
        runner, PrivateListsRunner
    ):
        raise ValueError("Time checks require the guarded private native reader.")
    checks["phase"] = "review_signed_release_time"
    reviewed = native.planner.preview((PackageRequest("cmake"),))
    releases = tuple(scope.lists.glob("*_dists_noble_InRelease"))
    if not reviewed.plan.ready or len(reviewed.prepared) != 1 or len(releases) != 1:
        raise ValueError("Signed-time checks require one authenticated native review.")
    created, expires = release_times(_read(releases[0], (scope.lists,)))
    moments = [("future", created - timedelta(seconds=11))]
    if expires is not None:
        moments.append(("expiry", expires + timedelta(seconds=1)))
    for name, moment in moments:
        inspector.clock = lambda: moment
        checks["phase"] = "reject_cached_release_" + name
        fresh = native.planner.preview((PackageRequest("cmake"),))
        if (
            fresh.plan.ready
            or not fresh.plan.issues
            or any(i.code != ErrorCode.VERIFICATION_FAILED for i in fresh.plan.issues)
        ):
            raise ValueError("Signed release time did not block fresh planning.")
        report = native.operations.execute(
            reviewed, consent=PlanConsent(reviewed, True, True)
        )
        if len(report.operations) != 1:
            raise ValueError("Time check did not return the reviewed operation.")
        result = report.operations[0].result
        if (
            result is None
            or result.state != OperationState.FAILED
            or result.error is None
            or result.error.code != ErrorCode.VERIFICATION_FAILED
            or result.exit_code is not None
            or report.persistence_errors
            or runner.mutation_attempts
        ):
            raise ValueError(
                "Signed release time did not reject stale approval before dispatch."
            )
        checks["cached_time_" + name] = "blocked_fresh_and_stale_plans"
    inspector.clock = utc_now
    restored = native.planner.preview((PackageRequest("cmake"),))
    if (
        not restored.plan.ready
        or restored.prepared[0].review != reviewed.prepared[0].review
    ):
        raise ValueError("The real review clock did not restore the same review.")
    checks["cached_time_expiry_field"] = (
        "present" if expires is not None else "absent_static_archive"
    )
    checks["cached_time_clock"] = "injected_reader_only_system_clock_unchanged"
    checks["cached_time_restore"] = "passed_same_review"
    checks["cached_time_refresh"] = "none"
