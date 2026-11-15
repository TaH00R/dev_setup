"""Small synchronous, ordered, session-local bus; no Qt or transport dependency."""

from collections import deque
from collections.abc import Callable
from datetime import datetime
from threading import RLock
from typing import TYPE_CHECKING, TypedDict

from models.diagnostics import DiagnosticCode, DiagnosticComponent
from models.errors import ErrorCode
from models.events import (
    EVENT_TYPES,
    EventDelivery,
    OperationChanged,
    OperationFailed,
    OperationFinished,
    PackageInstalled,
    PackageRemoved,
    PackageUpgraded,
)
from models.operations import Operation, OperationState
from models.planning import OperationAction
from system.diagnostics import record_diagnostic

if TYPE_CHECKING:
    from .records import AuditRecord

Subscriber = Callable[[EventDelivery], None]


class _Context(TypedDict):
    correlation_id: str
    execution_id: str
    operation_id: str
    package_id: str
    backend: str
    source_id: str
    native_id: str
    action: OperationAction
    state: OperationState
    timestamp: datetime
    error_code: ErrorCode | None


class Subscription:
    """Explicit ownership; close is idempotent, including inside a callback."""

    def __init__(self, bus: "EventBus", token: int) -> None:
        self._bus = bus
        self._token = token

    def close(self) -> None:
        self._bus.unsubscribe(self._token)

    def __enter__(self) -> "Subscription":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


class EventBus:
    """Callbacks run serially in the publishing thread and must return promptly.

    Nested publications queue behind the current delivery, so every subscriber
    observes the same sequence. Concurrent publishers serialize. A failing
    subscriber is removed; exceptions/context never escape into native results.
    There is no replay, persistence, global bus, or process-output channel.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._subscribers: dict[int, Subscriber] = {}
        self._next_token = 0
        self._sequence = 0
        self._pending: deque[EventDelivery] = deque()
        self._dispatching = False

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def subscribe(self, callback: Subscriber) -> Subscription:
        with self._lock:
            if not callable(callback):
                raise TypeError("Application event subscriber must be callable.")
            if len(self._subscribers) >= 64:
                raise ValueError("Application event subscriber limit reached.")
            self._next_token += 1
            self._subscribers[self._next_token] = callback
            return Subscription(self, self._next_token)

    def unsubscribe(self, token: int) -> None:
        with self._lock:
            self._subscribers.pop(token, None)

    def publish(self, event: OperationChanged) -> bool:
        if type(event) not in EVENT_TYPES:
            raise TypeError("Only registered application metadata is accepted.")
        with self._lock:
            if len(self._pending) >= 256:
                self._failure(event)
                return False
            self._sequence += 1
            self._pending.append(EventDelivery(self._sequence, event))
            if self._dispatching:
                return True
            self._dispatching = True
            try:
                # Bound reentrant feedback as well as queued storage.
                delivered = 0
                while self._pending and delivered < 1024:
                    delivery = self._pending.popleft()
                    delivered += 1
                    for token, callback in tuple(self._subscribers.items()):
                        if token not in self._subscribers:
                            continue
                        try:
                            callback(delivery)
                        except Exception:
                            self._subscribers.pop(token, None)
                            self._failure(delivery.event)
                if self._pending:
                    self._failure(self._pending[0].event)
                    self._pending.clear()
            finally:
                self._dispatching = False
            return True

    @staticmethod
    def _failure(event: OperationChanged) -> None:
        record_diagnostic(
            DiagnosticCode.OBSERVER_FAILED,
            DiagnosticComponent.OPERATIONS,
            operation_id=event.operation_id,
        )


def publish_operation(
    bus: EventBus,
    operation: Operation,
    correlation_id: str,
    execution_id: str,
    audit: "AuditRecord | None" = None,
) -> None:
    """Best-effort metadata projection; never replace the authoritative result."""
    try:
        result = operation.result
        event_type: type[OperationChanged] = OperationChanged
        if audit is not None:
            event_type = OperationFinished
            if operation.state == OperationState.FAILED:
                event_type = OperationFailed
            elif operation.state == OperationState.SUCCEEDED:
                event_type = {
                    OperationAction.INSTALL: PackageInstalled,
                    OperationAction.UNINSTALL: PackageRemoved,
                    OperationAction.UPGRADE: PackageUpgraded,
                }.get(operation.item.action, OperationFinished)
        context: _Context = dict(
            correlation_id=correlation_id,
            execution_id=execution_id,
            operation_id=operation.id,
            package_id=operation.item.package_id,
            backend=operation.item.spec.backend,
            source_id=operation.item.spec.source.id,
            native_id=operation.item.spec.native_id,
            action=operation.item.action,
            state=operation.state,
            timestamp=operation.updated_at,
            error_code=result.error.code if result and result.error else None,
        )
        # Separate constructors keep raw result/output out of the event schema.
        if audit is None:
            event = OperationChanged(**context)
        else:
            assert issubclass(event_type, OperationFinished)
            event = event_type(
                **context,
                version=audit.version,
                approved_at=audit.approved_at,
                exit_code=audit.exit_code,
            )
        bus.publish(event)
    except Exception:
        record_diagnostic(
            DiagnosticCode.OBSERVER_FAILED,
            DiagnosticComponent.OPERATIONS,
            operation_id=operation.id,
        )
