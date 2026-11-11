"""A single cancellable worker and queued GUI-thread delivery per window."""

from collections import deque
from collections.abc import Callable
from threading import Event, Lock

from PySide6.QtCore import QObject, QThread, Signal, Slot

from models.operations import ProgressEvent
from package_managers.base import AdapterError
from services.desktop import DesktopServices, DesktopSnapshot
from services.records import (
    AuditRecord,
    ExecutionReport,
    PackageRequest,
    PlanConsent,
    ReviewedPlan,
)
from system.commands import OutputSnapshot

Task = Callable[
    [Event, Callable[[ProgressEvent], None], Callable[[str, OutputSnapshot], None]],
    object,
]


class Job(QThread):
    result = Signal(object)
    error = Signal(str)
    progress = Signal(object)
    output_ready = Signal()

    def __init__(self, task: Task, parent: QObject) -> None:
        super().__init__(parent)
        self.task = task
        self.cancelled = Event()
        self._output_lock = Lock()
        self._outputs: dict[tuple[str, str], OutputSnapshot] = {}
        self._output_scheduled = False

    def publish_output(self, operation_id: str, snapshot: OutputSnapshot) -> None:
        # Queue one notice, not one large Qt event for every process read.
        bounded = OutputSnapshot(
            snapshot.stream,
            snapshot.text[-65536:],
            snapshot.truncated or len(snapshot.text) > 65536,
        )
        with self._output_lock:
            self._outputs[(operation_id, snapshot.stream)] = bounded
            while len(self._outputs) > 200:
                self._outputs.pop(next(iter(self._outputs)))
            notify = not self._output_scheduled
            self._output_scheduled = True
        if notify:
            self.output_ready.emit()

    def take_output(self) -> tuple[tuple[str, OutputSnapshot], ...]:
        with self._output_lock:
            snapshots = tuple((key[0], value) for key, value in self._outputs.items())
            self._outputs.clear()
            self._output_scheduled = False
        return snapshots

    def run(self) -> None:
        try:
            self.result.emit(
                self.task(self.cancelled, self.progress.emit, self.publish_output)
            )
        except AdapterError as error:
            self.error.emit(error.error.message)
        except Exception:
            self.error.emit(
                "The operation could not be completed. No rollback was attempted; partial changes may remain."
            )


class Controller(QObject):
    busy_changed = Signal(bool)
    snapshot_ready = Signal(object)
    updates_ready = Signal(object)
    plan_ready = Signal(object)
    execution_ready = Signal(object)
    progress = Signal(object)
    output = Signal(str, object)
    error = Signal(str)

    def __init__(
        self, services: DesktopServices, parent: QObject | None = None
    ) -> None:
        super().__init__(parent)
        self.services = services
        self.history: deque[AuditRecord] = deque(maxlen=200)
        self.job: Job | None = None
        self.kind = ""
        self.value: object = None
        self.failure = ""

    @property
    def busy(self) -> bool:
        return self.job is not None

    def start(self, kind: str, task: Task) -> bool:
        if self.busy:
            return False
        self.kind, self.value, self.failure = kind, None, ""
        job = Job(task, self)
        self.job = job
        job.result.connect(self._result)
        job.error.connect(self._error)
        job.progress.connect(self.progress)
        job.output_ready.connect(self._drain_output)
        job.finished.connect(self._finished)
        job.finished.connect(job.deleteLater)
        self.busy_changed.emit(True)
        job.start()
        return True

    @Slot(object)
    def _result(self, value: object) -> None:
        self.value = value

    @Slot(str)
    def _error(self, message: str) -> None:
        self.failure = message

    @Slot()
    def _drain_output(self) -> None:
        if self.job:
            for operation_id, snapshot in self.job.take_output():
                self.output.emit(operation_id, snapshot)

    @Slot()
    def _finished(self) -> None:
        kind, value, failure = self.kind, self.value, self.failure
        self.job = None
        self.value = None
        self.busy_changed.emit(False)
        if failure:
            self.error.emit(failure)
        elif kind == "refresh":
            self.snapshot_ready.emit(value)
        elif kind == "updates":
            self.updates_ready.emit(value)
        elif kind == "plan":
            self.plan_ready.emit(value)
        elif kind == "execute":
            if isinstance(value, ExecutionReport):
                self.history.extend(value.audit)
            self.execution_ready.emit(value)

    def cancel(self) -> None:
        if self.job:
            self.job.cancelled.set()

    def refresh(self) -> bool:
        return self.start(
            "refresh", lambda cancel, event, output: self.services.refresh(cancel)
        )

    def updates(self, snapshot: DesktopSnapshot) -> bool:
        return self.start(
            "updates",
            lambda cancel, event, output: self.services.updates(snapshot, cancel),
        )

    def preview(self, request: PackageRequest) -> bool:
        return self.start(
            "plan",
            lambda cancel, event, output: self.services.planner.preview(
                (request,), cancel=cancel
            ),
        )

    def execute(self, reviewed: ReviewedPlan, consent: PlanConsent | None) -> bool:
        return self.start(
            "execute",
            lambda cancel, event, output: self.services.operations.execute(
                reviewed, consent=consent, cancel=cancel, observe=event, logs=output
            ),
        )
