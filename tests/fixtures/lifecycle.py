"""Create journaled fake native work; no manager commands or real app data."""

from uuid import uuid4

from models.lifecycle import LifecycleState
from models.operations import Operation
from services.lifecycle import package_run
from services.records import PackageRequest, PlanConsent


def active_run(fixture, store, packages=("git",)):
    reviewed = fixture.services.planner.preview(
        tuple(PackageRequest(package) for package in packages)
    )
    consent = PlanConsent(reviewed, True)
    operations = tuple(Operation(uuid4().hex, item) for item in reviewed.plan.items)
    run = package_run(reviewed, consent, operations, store)
    operation_id = operations[0].id
    for state in (
        LifecycleState.RESOLVING,
        LifecycleState.AWAITING_CONSENT,
        LifecycleState.INSTALLING,
    ):
        run.transition(operation_id, state)
    return run, operation_id
