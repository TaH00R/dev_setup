"""Fixed diagnostic codes and bounded metadata; never exception text or output."""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ._validation import identifier
from .errors import ErrorCode
from .planning import aware_timestamp, utc_now


class DiagnosticCode(str, Enum):
    STARTUP_FAILED = "startup_failed"
    WORKER_FAILED = "worker_failed"
    STATE_IO_FAILED = "state_io_failed"
    EVENT_LOG_FAILED = "event_log_failed"
    INTERNAL_OPERATION_FAILED = "internal_operation_failed"
    PROCESS_START_FAILED = "process_start_failed"
    PROCESS_CONTROL_FAILED = "process_control_failed"
    PROCESS_MONITOR_FAILED = "process_monitor_failed"
    PIPE_READ_FAILED = "pipe_read_failed"
    PIPE_CLEANUP_FAILED = "pipe_cleanup_failed"
    OBSERVER_FAILED = "observer_failed"


class DiagnosticComponent(str, Enum):
    STARTUP = "startup"
    WORKER = "worker"
    STATE = "state"
    RUNNER = "runner"
    OPERATIONS = "operations"
    RUNTIMES = "runtimes"


@dataclass(frozen=True)
class DiagnosticEvent:
    code: DiagnosticCode
    component: DiagnosticComponent
    error_code: ErrorCode | None = None
    operation_id: str | None = None
    timestamp: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not isinstance(self.code, DiagnosticCode) or not isinstance(
            self.component, DiagnosticComponent
        ):
            raise ValueError("Diagnostics require registered codes and components.")
        if self.error_code is not None and not isinstance(self.error_code, ErrorCode):
            raise ValueError("Diagnostic errors must be registered codes.")
        if self.operation_id is not None:
            identifier(self.operation_id, "diagnostic operation id")
            if len(self.operation_id) > 128:
                raise ValueError("Diagnostic operation id exceeds its bound.")
        aware_timestamp(self.timestamp, "diagnostic timestamp")
