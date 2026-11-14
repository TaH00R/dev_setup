"""OperationPresenter coordinates widgets and existing services at the Qt boundary."""

from collections import OrderedDict
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Slot
from PySide6.QtWidgets import (
    QDialog,
    QTableWidgetItem,
)

from models.operations import OperationState, ProgressEvent
from services.records import ExecutionReport, ReviewedPlan
from system.commands import OutputSnapshot

from .review import ReviewDialog

if TYPE_CHECKING:
    from .main_window import MainWindow


class OperationPresenter(QObject):
    def __init__(self, view: "MainWindow") -> None:
        super().__init__(view)
        self.view = view

    @Slot(object)
    def _review(self, reviewed: ReviewedPlan) -> None:
        if self.view.pending_close or self.view.state_loading:
            self.view.controller.release_review()
            return
        if (
            not self.view.controller.reviewing
            and not self.view.controller.reserve_review()
        ):
            self.view.status.setText(
                "Finish or cancel the current environment operation before reviewing another plan."
            )
            return
        dialog = ReviewDialog(reviewed, self.view)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted and reviewed.plan.ready:
                consent = dialog.consent()
                if reviewed.plan.requires_consent and consent is None:
                    return
                self.view.navigation.setCurrentRow(4)
                if self.view.controller.execute(reviewed, consent):
                    for item in reviewed.plan.items:
                        placeholder = f"pending-{reviewed.plan.id}-{item.package_id}"
                        self.view._event(
                            ProgressEvent(
                                placeholder,
                                item.package_id,
                                OperationState.QUEUED,
                                "Waiting for this plan's preceding operations.",
                            )
                        )
                        self.view.pending_packages[item.package_id] = placeholder
            else:
                self.view.status.setText(
                    "Plan review closed. No package changes were started."
                )
        finally:
            self.view.controller.release_review()
            dialog.deleteLater()

    @Slot(object)
    def _event(self, event: ProgressEvent) -> None:
        placeholder = self.view.pending_packages.pop(event.package_id, None)
        if placeholder in self.view.operation_rows:
            self.view.operation_rows = OrderedDict(
                (event.operation_id if key == placeholder else key, row)
                for key, row in self.view.operation_rows.items()
            )
        if event.operation_id not in self.view.operation_rows:
            if len(self.view.operation_rows) >= 100:
                self.view.queue_table.removeRow(0)
                self.view.operation_rows.popitem(last=False)
                self.view.operation_rows = OrderedDict(
                    (key, value - 1) for key, value in self.view.operation_rows.items()
                )
            self.view.operation_rows[event.operation_id] = (
                self.view.queue_table.rowCount()
            )
            self.view.queue_table.insertRow(self.view.queue_table.rowCount())
        row = self.view.operation_rows[event.operation_id]
        for column, value in enumerate(
            (
                event.package_id,
                event.state.value,
                f"{event.percent:g}%" if event.percent is not None else "—",
                event.message,
            )
        ):
            self.view.queue_table.setItem(row, column, QTableWidgetItem(value))
        if self.view.queue_table.currentRow() < 0:
            self.view.queue_table.selectRow(row)
        self.view.status.setText(f"{event.package_id}: {event.message}")
        self.view._show_log()

    @Slot(str, object)
    def _output(self, operation_id: str, snapshot: OutputSnapshot) -> None:
        streams = self.view.log_cache.setdefault(operation_id, {})
        streams[snapshot.stream] = snapshot.text[-65536:] + (
            "\n[Output truncated]" if snapshot.truncated else ""
        )
        while len(self.view.log_cache) > 100:
            self.view.log_cache.popitem(last=False)
        self.view._show_log()

    def _show_log(self) -> None:
        index = self.view.queue_table.currentRow()
        operation_id = next(
            (key for key, row in self.view.operation_rows.items() if row == index), None
        )
        streams = self.view.log_cache.get(operation_id, {}) if operation_id else {}
        self.view.log_view.setPlainText(
            "\n\n".join(f"{stream.upper()}\n{text}" for stream, text in streams.items())
        )

    @Slot(object)
    def _completed(self, report: ExecutionReport) -> None:
        self.view._merge_history(report.audit)
        if self.view.state_path is not None and report.audit:
            self.view.state_controller.append_audit(report.audit)
        for operation in report.operations:
            if operation.result:
                result = operation.result
                message = result.message + (
                    f" Exit: {result.exit_code}."
                    if result.exit_code is not None
                    else ""
                )
                if result.error:
                    message += f" {result.error.code.value}: {result.error.message}"
                self.view._event(
                    ProgressEvent(
                        operation.id,
                        operation.item.package_id,
                        operation.state,
                        message,
                    )
                )
                self.view._output(
                    operation.id,
                    OutputSnapshot("stdout", result.stdout, result.output_truncated),
                )
                self.view._output(
                    operation.id,
                    OutputSnapshot("stderr", result.stderr, result.output_truncated),
                )
        self.view._history_rows()
        self.view.status.setText(
            f"Plan finished. Outcome: {report.outcome.value}. Review Queue and refresh inventory."
            + (
                " " + " ".join(report.persistence_errors)
                if report.persistence_errors
                else ""
            )
        )
