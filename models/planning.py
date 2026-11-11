"""Immutable review records; creating a plan executes nothing and grants no consent."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from ._validation import enum_value, identifier, text, unique_tuple
from .errors import OperationError
from .platform import Platform
from .specs import InstallSpec


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def aware_timestamp(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")


class OperationAction(str, Enum):
    INSTALL = "install"
    UNINSTALL = "uninstall"
    UPGRADE = "upgrade"
    VERIFY = "verify"


@dataclass(frozen=True)
class CommandPreview:
    """Adapter-supplied redacted display arguments, never input to an executor."""

    display_args: tuple[str, ...]
    description: str

    def __post_init__(self) -> None:
        if not isinstance(self.display_args, tuple) or not self.display_args:
            raise ValueError("command preview requires an immutable argument tuple")
        for arg in self.display_args:
            text(arg, "display argument")
        text(self.description, "command description")


@dataclass(frozen=True)
class PlanItem:
    package_id: str
    action: OperationAction
    spec: InstallSpec
    commands: tuple[CommandPreview, ...] = ()
    dependencies: tuple[str, ...] = ()
    changes: tuple[str, ...] = ()
    skip_reason: str | None = None

    def __post_init__(self) -> None:
        identifier(self.package_id, "plan package id")
        enum_value(self.action, OperationAction, "operation action")
        if not isinstance(self.spec, InstallSpec):
            raise ValueError("plan item requires an InstallSpec")
        unique_tuple(self.commands, "command previews")
        if any(not isinstance(item, CommandPreview) for item in self.commands):
            raise ValueError("commands must contain CommandPreview records")
        unique_tuple(self.dependencies, "plan dependencies")
        for dependency in self.dependencies:
            identifier(dependency, "plan dependency")
        if self.package_id in self.dependencies:
            raise ValueError("a plan item cannot depend on itself")
        unique_tuple(self.changes, "planned changes")
        for change in self.changes:
            text(change, "planned change")
        if self.skip_reason is not None:
            text(self.skip_reason, "skip reason")
            if self.commands:
                raise ValueError(
                    "skipped items cannot have executable command previews"
                )
        elif not self.commands:
            raise ValueError("a non-skipped item requires command previews")

    @property
    def requires_consent(self) -> bool:
        return self.skip_reason is None and self.action != OperationAction.VERIFY


@dataclass(frozen=True)
class InstallPlan:
    id: str
    platform: Platform
    items: tuple[PlanItem, ...]
    issues: tuple[OperationError, ...] = ()
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        identifier(self.id, "plan id")
        if not isinstance(self.platform, Platform):
            raise ValueError("plan platform must be a Platform")
        aware_timestamp(self.created_at, "plan creation time")
        unique_tuple(self.items, "plan items")
        if any(not isinstance(item, PlanItem) for item in self.items):
            raise ValueError("plan items must contain PlanItem records")
        ids = tuple(item.package_id for item in self.items)
        unique_tuple(ids, "plan package ids")
        # A valid plan is already dependency-ordered; the resolver comes later.
        preceding: set[str] = set()
        for item in self.items:
            if not item.spec.support.supports(self.platform):
                raise ValueError(f"plan platform does not support {item.package_id}")
            if not set(item.dependencies) <= preceding:
                raise ValueError("plan dependencies must precede dependent items")
            preceding.add(item.package_id)
        unique_tuple(self.issues, "plan issues")
        if any(not isinstance(issue, OperationError) for issue in self.issues):
            raise ValueError("plan issues must contain OperationError records")

    @property
    def ready(self) -> bool:
        return bool(self.items) and not self.issues

    @property
    def requires_consent(self) -> bool:
        return any(item.requires_consent for item in self.items)
