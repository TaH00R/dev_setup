"""Workflow support is separate from adapter command construction and presence."""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from models.planning import OperationAction
from models.specs import InstallSpec

MutationPolicy = Callable[[InstallSpec, OperationAction], str | None]


class SupportLevel(str, Enum):
    COMMAND_ONLY = "command_only"
    READ_ONLY = "read_only"
    UNQUALIFIED = "unqualified"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class WorkflowSupport:
    backend: str
    action: OperationAction
    level: SupportLevel
    reason: str


def pending_native_qualification(
    spec: InstallSpec, action: OperationAction
) -> str | None:
    if action == OperationAction.VERIFY:
        return None
    # No native qualification evidence has yet been reviewed for any scope.
    # A successful smoke artifact must never silently enable a production action.
    return (
        f"{spec.backend} {action.value} is awaiting native qualification for this platform and source. "
        "Search, inventory and read-only verification remain available."
    )


def workflow_support(
    backend: str, action: OperationAction, *, inspector: bool, command: bool
) -> WorkflowSupport:
    if not inspector:
        return WorkflowSupport(
            backend,
            action,
            SupportLevel.COMMAND_ONLY if command else SupportLevel.UNSUPPORTED,
            "Discovery and command construction only; package management is unavailable.",
        )
    if action == OperationAction.VERIFY:
        return WorkflowSupport(
            backend,
            action,
            SupportLevel.READ_ONLY,
            "Read-only installed-package verification is implemented.",
        )
    if not command:
        return WorkflowSupport(
            backend,
            action,
            SupportLevel.UNSUPPORTED,
            "The native backend does not support this action.",
        )
    return WorkflowSupport(
        backend,
        action,
        SupportLevel.UNQUALIFIED,
        "Review and verification are implemented; native installation, update and removal qualification is pending.",
    )
