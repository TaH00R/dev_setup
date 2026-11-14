"""Cancellable environment jobs with a reserved review slot and queued Qt results."""

from collections.abc import Callable

from PySide6.QtCore import QObject, Signal, Slot

from .workers import Job, Task


class EnvironmentController(QObject):
    busy_changed = Signal(bool)
    completed = Signal(str, object)
    error = Signal(str)
    output = Signal(str, object)

    def __init__(self, can_start: Callable[[], bool], parent: QObject) -> None:
        super().__init__(parent)
        self.can_start = can_start
        self.job: Job | None = None
        self.reviewing = False
        self.kind = ""
        self.value: object = None
        self.failure = ""

    @property
    def busy(self) -> bool:
        return self.job is not None or self.reviewing

    def start(self, kind: str, task: Task, *, from_review: bool = False) -> bool:
        if (
            self.job is not None
            or (self.reviewing and not from_review)
            or not self.can_start()
        ):
            return False
        self.reviewing = False
        self.kind, self.value, self.failure = kind, None, ""
        job = Job(task, self)
        self.job = job
        job.result.connect(self._result)
        job.error.connect(self._error)
        job.output_ready.connect(self._logs)
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
    def _logs(self) -> None:
        if self.job:
            for operation_id, snapshot in self.job.take_output():
                self.output.emit(operation_id, snapshot)

    @Slot()
    def _finished(self) -> None:
        kind, value, failure = self.kind, self.value, self.failure
        if (
            self.job
            and self.job.cancelled.is_set()
            and kind in {"runtime-preview", "config-preview"}
        ):
            value, failure = (
                None,
                "Environment review preparation was cancelled. No changes were started.",
            )
        self.job, self.value = None, None
        self.reviewing = not failure and kind in {"runtime-preview", "config-preview"}
        self.busy_changed.emit(self.busy)
        if failure:
            self.error.emit(failure)
        else:
            self.completed.emit(kind, value)

    def release_review(self) -> None:
        if self.reviewing:
            self.reviewing = False
            self.busy_changed.emit(self.busy)

    def cancel(self) -> None:
        if self.job:
            self.job.cancelled.set()
