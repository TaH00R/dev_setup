"""Independent live-search worker: latest request wins, one active job at a time."""

from dataclasses import replace
from threading import Event, Lock

from PySide6.QtCore import QObject, QThread, Signal, Slot

from models.discovery import SearchState
from services.discovery import DiscoveryService, SearchSnapshot


class SearchJob(QThread):
    ready = Signal()
    failed = Signal(str)

    def __init__(
        self,
        service: DiscoveryService,
        generation: int,
        query: str,
        category: str | None,
        mapped_only: bool,
        force: bool,
        parent: QObject,
    ) -> None:
        super().__init__(parent)
        self.service, self.generation, self.query = service, generation, query
        self.category, self.mapped_only, self.force = category, mapped_only, force
        self.cancelled = Event()
        self._lock = Lock()
        self._latest: SearchSnapshot | None = None
        self._notified = False

    def publish(self, snapshot: SearchSnapshot) -> None:
        with self._lock:
            self._latest = snapshot
            notify = not self._notified
            self._notified = True
        if notify:
            self.ready.emit()

    def take(self) -> SearchSnapshot | None:
        with self._lock:
            value = self._latest
            self._latest, self._notified = None, False
        return value

    def run(self) -> None:
        try:
            self.service.search(
                self.query,
                self.cancelled,
                category=self.category,
                mapped_only=self.mapped_only,
                force=self.force,
                observe=self.publish,
            )
        except Exception:
            self.failed.emit(
                "Live search could not complete; curated results remain available."
            )


class SearchController(QObject):
    ready = Signal(object)
    busy_changed = Signal(bool)
    error = Signal(str)

    def __init__(self, service: DiscoveryService, parent: QObject) -> None:
        super().__init__(parent)
        self.service = service
        self.generation = 0
        self.job: SearchJob | None = None
        self.pending: tuple[int, str, str | None, bool, bool] | None = None
        self.latest: SearchSnapshot | None = None
        self._dispatch_allowed = False

    @property
    def busy(self) -> bool:
        return self.job is not None

    def request(
        self,
        query: str,
        *,
        category: str | None = None,
        mapped_only: bool = False,
        enabled: bool = True,
        deferred: bool = False,
        force: bool = False,
    ) -> None:
        self.generation += 1
        if self.job:
            self.job.cancelled.set()
        initial = self.service.initial(
            query, category=category, mapped_only=mapped_only, enabled=enabled
        )
        self.latest = initial
        self._dispatch_allowed = not deferred
        self.pending = (
            None
            if initial.complete
            else (self.generation, initial.query, category, mapped_only, force)
        )
        self.ready.emit(initial)
        if not deferred:
            self.dispatch()

    @Slot()
    def dispatch(self) -> None:
        self._dispatch_allowed = True
        self._start_pending()

    def _start_pending(self) -> None:
        if self.job is not None or self.pending is None or not self._dispatch_allowed:
            return
        generation, query, category, mapped_only, force = self.pending
        self.pending = None
        job = SearchJob(
            self.service, generation, query, category, mapped_only, force, self
        )
        self.job = job
        job.ready.connect(self._consume)
        job.failed.connect(self._failed)
        job.finished.connect(self._finished)
        job.finished.connect(job.deleteLater)
        self.busy_changed.emit(True)
        job.start()

    @Slot()
    def _consume(self) -> None:
        if self.job:
            value = self.job.take()
            if value is not None and self.job.generation == self.generation:
                self.latest = value
                self.ready.emit(value)

    @Slot(str)
    def _failed(self, message: str) -> None:
        if self.job and self.job.generation == self.generation:
            self.error.emit(message)

    @Slot()
    def _finished(self) -> None:
        self._consume()
        self.job = None
        self.busy_changed.emit(False)
        self._start_pending()

    def cancel(self) -> None:
        self.pending = None
        if self.job:
            self.job.cancelled.set()
        if self.latest and not self.latest.complete:
            self.latest = replace(
                self.latest,
                cancelled=True,
                providers=tuple(
                    replace(p, state=SearchState.CANCELLED)
                    if p.state == SearchState.SEARCHING
                    else p
                    for p in self.latest.providers
                ),
            )
            self.ready.emit(self.latest)
