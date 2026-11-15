"""Bounded, explicitly queued bridge from core metadata to the GUI thread."""

from collections import deque
from threading import Lock

from PySide6.QtCore import QObject, Qt, Signal, Slot

from models.events import EventDelivery
from services.events import EventBus


class EventBridge(QObject):
    delivered = Signal(object)
    overflow = Signal(int)
    _wake = Signal()

    def __init__(self, bus: EventBus, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._lock = Lock()
        self._pending: deque[EventDelivery] = deque(maxlen=512)
        self._scheduled = False
        self._closed = False
        self._dropped = 0
        self._draining = False
        self._wake.connect(self._drain, Qt.ConnectionType.QueuedConnection)
        self._subscription = bus.subscribe(self._receive)
        # A QObject owner being deleted must also release the core callback.
        subscription = self._subscription
        self.destroyed.connect(lambda: subscription.close())

    def _receive(self, delivery: EventDelivery) -> None:
        with self._lock:
            if self._closed:
                return
            if len(self._pending) == self._pending.maxlen:
                self._dropped += 1
            self._pending.append(delivery)
            wake = not self._scheduled
            self._scheduled = True
        if wake:
            self._wake.emit()

    @Slot()
    def _drain(self) -> None:
        if self._draining:
            return
        with self._lock:
            if self._closed:
                return
            deliveries = tuple(self._pending)
            dropped = self._dropped
            self._pending.clear()
            self._scheduled = False
            self._dropped = 0
        self._draining = True
        try:
            if dropped:
                self.overflow.emit(dropped)
            for delivery in deliveries:
                if self._closed:
                    break
                self.delivered.emit(delivery)
        finally:
            self._draining = False
            # A receiver can process nested Qt events. Re-arm a wake consumed
            # during that nested loop without delivering out of sequence.
            with self._lock:
                wake = bool(self._pending) and not self._closed
            if wake:
                self._wake.emit()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._pending.clear()
        self._subscription.close()

    def flush(self) -> None:
        """GUI-thread barrier before consuming the final execution report."""
        self._drain()
