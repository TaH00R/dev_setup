"""Opt-in diagnostic exports and background signed application update checks."""

from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app import __version__
from models.platform import Platform
from package_managers.base import Availability
from services.diagnostics import (
    DiagnosticReport,
    collect_diagnostics,
    diagnostic_json,
    export_diagnostics,
)
from services.updates import UpdateCheck, UpdateService

from .environments import EnvironmentController


class SupportControls(QObject):
    busy_changed = Signal(bool)

    def __init__(self, host: Platform, parent: QWidget) -> None:
        super().__init__(parent)
        self.host = host
        self.availability: tuple[Availability, ...] = ()
        self.report: DiagnosticReport | None = None
        self.updates = UpdateService()
        self.controller = EnvironmentController(lambda: True, self)
        self.controller.completed.connect(self._completed)
        self.controller.error.connect(self._error)
        self.controller.busy_changed.connect(self._busy)
        self.group = QGroupBox("Support and application updates")
        layout = QVBoxLayout(self.group)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(120)
        self.details.setPlaceholderText(
            "Preview exports only app/Python versions, platform identifiers and backend availability. No paths, logs or environment values."
        )
        layout.addWidget(self.details)
        row = QHBoxLayout()
        self.preview = QPushButton("Preview diagnostics")
        self.preview.clicked.connect(self.preview_diagnostics)
        row.addWidget(self.preview)
        self.export = QPushButton("Export reviewed diagnostics")
        self.export.setEnabled(False)
        self.export.clicked.connect(self._export_dialog)
        row.addWidget(self.export)
        self.check = QPushButton("Check application updates")
        self.check.clicked.connect(self.check_updates)
        row.addWidget(self.check)
        layout.addLayout(row)
        self.note = QLabel(
            "Application updates are disabled: no trusted signed release channel is configured. Checks are manual; automatic installation is unavailable."
        )
        self.note.setTextFormat(Qt.TextFormat.PlainText)
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

    def preview_diagnostics(self) -> None:
        availability = self.availability
        self.controller.start(
            "diagnostics",
            lambda cancel, event, output: collect_diagnostics(
                __version__, self.host, availability
            ),
        )

    def _export_dialog(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self.group,
            "Export reviewed diagnostics to a new file",
            "dev-setup-diagnostics.json",
            "JSON (*.json)",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if path:
            self.export_to(Path(path))

    def export_to(self, path: Path) -> bool:
        report = self.report
        if report is None:
            return False
        return self.controller.start(
            "diagnostic-export",
            lambda cancel, event, output: export_diagnostics(report, path),
        )

    def check_updates(self) -> None:
        self.controller.start(
            "update-check",
            lambda cancel, event, output: self.updates.check(
                __version__, self.host, cancel
            ),
        )

    def _completed(self, kind: str, value: object) -> None:
        if kind == "diagnostics" and isinstance(value, DiagnosticReport):
            self.report = value
            self.details.setPlainText(diagnostic_json(value))
            self.export.setEnabled(True)
            self.note.setText(
                "Diagnostics are ready for review. Backend facts reflect the last inventory refresh; export creates a new file only."
            )
        elif kind == "diagnostic-export":
            self.note.setText("The reviewed diagnostic report was exported.")
        elif kind == "update-check" and isinstance(value, UpdateCheck):
            text = value.message
            if value.offer:
                artifact = value.offer.artifact
                text += f" Version {value.offer.release.version}; {artifact.filename}; {artifact.size} bytes. Install manually through your trusted distribution channel."
            self.note.setText(text)

    def _error(self, message: str) -> None:
        self.note.setText(message)

    def _busy(self, busy: bool) -> None:
        self.preview.setEnabled(not busy)
        self.export.setEnabled(not busy and self.report is not None)
        self.check.setEnabled(not busy)
        self.busy_changed.emit(busy)
