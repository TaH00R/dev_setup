"""Sanitized, structured failures shared by future services and adapters."""

from dataclasses import dataclass
from enum import Enum

from ._validation import enum_value, text, unique_tuple


class ErrorCode(str, Enum):
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    MISSING_BACKEND = "missing_backend"
    NETWORK = "network"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    COMMAND_FAILED = "command_failed"
    DEPENDENCY_CONFLICT = "dependency_conflict"
    DEPENDENCY_CYCLE = "dependency_cycle"
    VERSION_MISMATCH = "version_mismatch"
    VERIFICATION_FAILED = "verification_failed"
    PERMISSION_DENIED = "permission_denied"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class OperationError:
    code: ErrorCode
    message: str
    retryable: bool = False
    details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        enum_value(self.code, ErrorCode, "error code")
        text(self.message, "error message")
        if type(self.retryable) is not bool:
            raise ValueError("retryable must be a boolean")
        unique_tuple(self.details, "error details")
        for detail in self.details:
            text(detail, "error detail")
