"""Serialized, asynchronous application state I/O; failures preserve session results."""

from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QThread, Signal, Slot

from persistence.events import SafeEventLog
from persistence.store import DesktopSettings, StateError, StateStore
from services.records import AuditRecord


@dataclass(frozen=True)
class StoredState:
    store: StateStore = field(repr=False)
    settings: DesktopSettings
    history: tuple[AuditRecord, ...]
    event_log: SafeEventLog | None = field(default=None, repr=False)


class StateJob(QThread):
    def __init__(self, task: Callable[[], object], parent: QObject) -> None:
        super().__init__(parent)
        self.task = task
        self.value: object = None
        self.failure = ""

    def run(self) -> None:
        try:
            self.value = self.task()
        except StateError as error:
            self.failure = str(error)
        except Exception:
            self.failure = "Application state could not be saved. Current results remain in this session."


class StateController(QObject):
    loaded = Signal(object)
    saved = Signal(str)
    error = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.store: StateStore | None = None
        self.event_log: SafeEventLog | None = None
        self.path: Path | None = None
        self.job: StateJob | None = None
        self.failure = ""
        self._kind = ""
        self._pending: OrderedDict[str, tuple[str, Callable[[], object]]] = (
            OrderedDict()
        )
        self._counter = 0

    @property
    def busy(self) -> bool:
        return self.job is not None or bool(self._pending)

    def startup(self, path: Path) -> bool:
        if self.busy:
            return False
        self.path, self.failure = Path(path), ""
        if self.store:
            self.store.close()
        if self.event_log:
            self.event_log.close()
        self.event_log = None
        self.store = None

        def load() -> StoredState:
            store = StateStore(path)
            try:
                settings, history = store.load_settings(), store.load_history()
                try:
                    event_log = SafeEventLog(path.with_name("events.log"))
                    event_log.record("state-opened")
                except (StateError, OSError):
                    # An optional log must not make valid saved state unusable.
                    event_log = None
                return StoredState(store, settings, history, event_log)
            except Exception:
                store.close()
                raise

        return self._enqueue("load", load)

    def _enqueue(self, kind: str, task: Callable[[], object]) -> bool:
        if self.failure:
            self.error.emit(self.failure)
            return False
        if len(self._pending) >= 64:
            self.error.emit(
                "The state save queue is full. Current results remain in this session."
            )
            return False
        self._counter += 1
        key = kind if kind in {"settings", "clear"} else f"{kind}-{self._counter}"
        self._pending[key] = kind, task
        self._pending.move_to_end(key)
        self._dispatch()
        return True

    def _dispatch(self) -> None:
        if self.job or not self._pending:
            return
        _, (self._kind, task) = self._pending.popitem(last=False)
        kind = self._kind

        def logged_task() -> object:
            try:
                value = task()
            except Exception:
                self._record("state-failed", kind)
                raise
            self._record("state-saved", kind)
            return value

        self.job = StateJob(logged_task, self)
        self.job.finished.connect(self._finished)
        self.job.finished.connect(self.job.deleteLater)
        self.busy_changed.emit(True)
        self.job.start()

    @Slot()
    def _finished(self) -> None:
        job = self.job
        if job is None:
            return
        kind = self._kind
        self.job = None
        if job.failure:
            self.failure = job.failure
            self._pending.clear()
            self.error.emit(self.failure)
        elif kind == "load" and isinstance(job.value, StoredState):
            self.store = job.value.store
            self.event_log = job.value.event_log
            self.loaded.emit(job.value)
        else:
            self.saved.emit(kind)
        self._dispatch()
        if not self.busy:
            self.busy_changed.emit(False)

    def _store(self) -> StateStore:
        if self.store is None:
            raise StateError(
                "Saved state is unavailable. Current results remain in this session."
            )
        return self.store

    def _record(self, event: str, kind: str) -> None:
        if self.event_log is not None:
            try:
                self.event_log.record(event, code=kind)
            except Exception:
                # Log failure cannot replace an operation or persistence result.
                pass

    def save_settings(self, settings: DesktopSettings) -> bool:
        return self._enqueue("settings", lambda: self._store().save_settings(settings))

    def append_audit(self, records: tuple[AuditRecord, ...]) -> bool:
        return self._enqueue("audit", lambda: self._store().append_audit(records))

    def clear_history(self) -> bool:
        return self._enqueue("clear", lambda: self._store().clear_history())

    def flush(self) -> None:
        """After app.exec exits, retain objects until all owned DB work completes."""
        while self.job is not None:
            self.job.wait()
            QCoreApplication.processEvents()

    def close(self) -> None:
        self.flush()
        if self.store:
            self.store.close()
        if self.event_log:
            self.event_log.close()
