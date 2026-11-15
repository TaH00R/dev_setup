"""Immutable application metadata. Process output never belongs in this stream."""

import re
from dataclasses import dataclass, field
from datetime import datetime

from ._validation import enum_value, identifier
from .errors import ErrorCode
from .operations import OperationState
from .planning import OperationAction, aware_timestamp, utc_now


@dataclass(frozen=True, kw_only=True)
class OperationChanged:
    correlation_id: str  # reviewed plan; shared by retries of that plan
    execution_id: str  # one execution of the reviewed plan
    operation_id: str
    package_id: str
    backend: str
    source_id: str
    native_id: str
    action: OperationAction
    state: OperationState
    timestamp: datetime = field(default_factory=utc_now)
    error_code: ErrorCode | None = None

    def __post_init__(self) -> None:
        for name in (
            "correlation_id",
            "execution_id",
            "operation_id",
            "package_id",
            "backend",
            "source_id",
        ):
            value = getattr(self, name)
            identifier(value, name)
            if len(value) > 256:
                raise ValueError("Application event identifier exceeds its bound.")
        if (
            not isinstance(self.native_id, str)
            or len(self.native_id) > 256
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+@/:~-]*", self.native_id) is None
        ):
            raise ValueError("Event native identity must be bounded metadata.")
        enum_value(self.action, OperationAction, "event action")
        enum_value(self.state, OperationState, "event state")
        aware_timestamp(self.timestamp, "event timestamp")
        if self.error_code is not None:
            enum_value(self.error_code, ErrorCode, "event error code")


@dataclass(frozen=True, kw_only=True)
class OperationFinished(OperationChanged):
    version: str | None = None
    approved_at: datetime | None = None
    exit_code: int | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.state.terminal:
            raise ValueError("Finished events require a terminal result.")
        if self.version is not None and (
            not isinstance(self.version, str)
            or not self.version
            or len(self.version) > 256
            or any(ord(c) < 32 for c in self.version)
        ):
            raise ValueError("Event version must be bounded metadata.")
        if self.approved_at is not None:
            aware_timestamp(self.approved_at, "event approval")
            if self.approved_at > self.timestamp:
                raise ValueError("Event approval cannot follow completion.")
        if self.exit_code is not None and type(self.exit_code) is not int:
            raise ValueError("Event exit code must be an integer.")
        if self.state == OperationState.SUCCEEDED and (
            self.exit_code not in (None, 0) or self.error_code is not None
        ):
            raise ValueError("Success metadata cannot contain a failure.")


@dataclass(frozen=True, kw_only=True)
class PackageInstalled(OperationFinished):
    def __post_init__(self) -> None:
        super().__post_init__()
        if (
            self.action != OperationAction.INSTALL
            or self.state != OperationState.SUCCEEDED
        ):
            raise ValueError("PackageInstalled requires a verified installation.")


@dataclass(frozen=True, kw_only=True)
class PackageRemoved(OperationFinished):
    def __post_init__(self) -> None:
        super().__post_init__()
        if (
            self.action != OperationAction.UNINSTALL
            or self.state != OperationState.SUCCEEDED
        ):
            raise ValueError("PackageRemoved requires verified removal.")


@dataclass(frozen=True, kw_only=True)
class PackageUpgraded(OperationFinished):
    def __post_init__(self) -> None:
        super().__post_init__()
        if (
            self.action != OperationAction.UPGRADE
            or self.state != OperationState.SUCCEEDED
        ):
            raise ValueError("PackageUpgraded requires a verified upgrade.")


@dataclass(frozen=True, kw_only=True)
class OperationFailed(OperationFinished):
    def __post_init__(self) -> None:
        super().__post_init__()
        if self.state != OperationState.FAILED or self.error_code is None:
            raise ValueError("OperationFailed requires a structured failure code.")


EVENT_TYPES = (
    OperationChanged,
    OperationFinished,
    PackageInstalled,
    PackageRemoved,
    PackageUpgraded,
    OperationFailed,
)


@dataclass(frozen=True)
class EventDelivery:
    sequence: int
    event: OperationChanged

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("Event sequence must be positive.")
        if type(self.event) not in EVENT_TYPES:
            raise ValueError("Only registered application metadata is accepted.")
