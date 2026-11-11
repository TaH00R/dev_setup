"""Immutable service boundaries. Native versions are opaque, never semver guesses."""

from dataclasses import dataclass, field
from datetime import datetime

from models._validation import identifier
from models.inventory import InstalledPackage, NativeReview
from models.operations import Operation
from models.package import PackageVersion
from models.planning import InstallPlan, OperationAction, aware_timestamp, utc_now
from system.commands import Command


@dataclass(frozen=True)
class PackageRequest:
    package_id: str
    action: OperationAction = OperationAction.INSTALL
    backend: str | None = None
    source_id: str | None = None
    version: PackageVersion | None = None

    def __post_init__(self) -> None:
        identifier(self.package_id, "requested package")
        if not isinstance(self.action, OperationAction):
            raise ValueError("request requires an operation action")
        for value in (self.backend, self.source_id):
            if value is not None:
                identifier(value, "requested backend/source")
        if self.version is not None and not isinstance(self.version, PackageVersion):
            raise ValueError("request requires a PackageVersion")


@dataclass(frozen=True)
class PreparedItem:
    command: Command | None
    installed: InstalledPackage | None
    review: NativeReview | None


@dataclass(frozen=True)
class ReviewedPlan:
    plan: InstallPlan
    prepared: tuple[PreparedItem, ...]

    def __post_init__(self) -> None:
        if len(self.prepared) != len(self.plan.items):
            raise ValueError("prepared records must match plan items")
        for item, prepared in zip(self.plan.items, self.prepared):
            if item.requires_consent != (prepared.command is not None):
                raise ValueError("only mutations have prepared commands")
            if prepared.command is not None and item.commands != (
                prepared.command.preview(),
            ):
                raise ValueError("displayed and prepared commands must match")


@dataclass(frozen=True)
class PlanConsent:
    """UI/CLI must construct this only after a real affirmative review decision."""

    reviewed: ReviewedPlan = field(repr=False)
    confirmed: bool
    elevation: bool = False
    approved_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not isinstance(self.reviewed, ReviewedPlan):
            raise ValueError("consent requires an immutable reviewed plan")
        if type(self.confirmed) is not bool or type(self.elevation) is not bool:
            raise ValueError("consent decisions must be explicit booleans")
        aware_timestamp(self.approved_at, "approval time")
        if self.approved_at < self.reviewed.plan.created_at:
            raise ValueError("consent must follow plan review")


@dataclass(frozen=True)
class AuditRecord:
    """In-memory, output-free history; durable persistence belongs to step 10."""

    operation_id: str
    package_id: str
    action: str
    backend: str
    source_id: str
    version: str | None
    state: str
    approved_at: datetime | None
    finished_at: datetime
    exit_code: int | None


@dataclass(frozen=True)
class ExecutionReport:
    operations: tuple[Operation, ...]
    audit: tuple[AuditRecord, ...]
