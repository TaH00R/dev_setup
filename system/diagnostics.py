"""Session-local metadata ring; recording can never replace an operation result."""

from collections import deque
from threading import RLock

from models.diagnostics import DiagnosticCode, DiagnosticComponent, DiagnosticEvent
from models.errors import ErrorCode


class DiagnosticBuffer:
    def __init__(self, capacity: int = 128) -> None:
        if type(capacity) is not int or not 1 <= capacity <= 128:
            raise ValueError("Diagnostic capacity must be between 1 and 128.")
        self._events: deque[DiagnosticEvent] = deque(maxlen=capacity)
        self._lock = RLock()

    def record(
        self,
        code: DiagnosticCode,
        component: DiagnosticComponent,
        *,
        error_code: ErrorCode | None = None,
        operation_id: str | None = None,
    ) -> DiagnosticEvent:
        event = DiagnosticEvent(code, component, error_code, operation_id)
        with self._lock:
            self._events.append(event)
        return event

    def snapshot(self, limit: int = 32) -> tuple[DiagnosticEvent, ...]:
        if type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError("Diagnostic snapshot exceeds its bound.")
        with self._lock:
            return tuple(self._events)[-limit:]


session_diagnostics = DiagnosticBuffer()


def record_diagnostic(
    code: DiagnosticCode,
    component: DiagnosticComponent,
    *,
    error_code: ErrorCode | None = None,
    operation_id: str | None = None,
) -> DiagnosticEvent | None:
    try:
        return session_diagnostics.record(
            code, component, error_code=error_code, operation_id=operation_id
        )
    except Exception:
        return None
