"""SessionPresenter coordinates widgets and existing services at the Qt boundary."""

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import (
    QMessageBox,
)

from models.environment import RuntimeAudit
from persistence.store import DesktopSettings
from services.records import AuditRecord

from .components import fill_table
from .state import StoredState

if TYPE_CHECKING:
    from .main_window import MainWindow


class SessionPresenter(QObject):
    def __init__(self, view: "MainWindow") -> None:
        super().__init__(view)
        self.view = view

    def _history_rows(self) -> None:
        rows = list(reversed(self.view.controller.history))
        fill_table(
            self.view.history_table,
            [
                (
                    f"{row.package_id} / {row.action}",
                    f"{row.backend} / {row.source_id}",
                    row.version or "Unknown",
                    row.state,
                    row.finished_at.strftime("%Y-%m-%d %H:%M:%S"),
                )
                for row in rows
            ],
        )
        self.view.activity.setText(
            "\n".join(
                f"{row.package_id} · {row.action} · {row.state}" for row in rows[:5]
            )
            or "No operations in this session."
        )

    def _clear(self) -> None:
        if (
            self.view._operation_busy()
            or self.view.state_loading
            or self.view.clearing_history
        ):
            return
        if self.view.state_path is not None:
            if self.view.state_controller.busy:
                self.view.persistence_note.setText(
                    "Wait for saved history to finish before clearing it."
                )
                return
            answer = QMessageBox.question(
                self.view,
                "Clear saved history?",
                "Permanently remove saved approval/result history and current session logs? Installed tools are unaffected.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.view.clearing_history = True
            self.view._busy(self.view.controller.busy)
            self.view.state_controller.clear_history()
            return
        self.view._clear_session()

    def _clear_session(self) -> None:
        self.view.controller.history.clear()
        self.view.operation_rows.clear()
        self.view.pending_packages.clear()
        self.view.log_cache.clear()
        self.view.queue_table.setRowCount(0)
        self.view.log_view.clear()
        self.view._history_rows()
        self.view.status.setText("History and session logs cleared.")

    def _settings_enabled(self, enabled: bool) -> None:
        for widget in (
            self.view.theme,
            self.view.debounce,
            self.view.live_search_setting,
            self.view.live_enabled,
        ):
            widget.setEnabled(enabled)
        self.view.clear_button.setEnabled(enabled and not self.view._operation_busy())

    def _preferences_changed(self) -> None:
        if (
            self.view.state_path is None
            or self.view.state_loading
            or self.view.restoring_state
        ):
            return
        self.view.state_controller.save_settings(
            DesktopSettings(
                "dark" if self.view.theme.currentIndex() == 1 else "light",
                self.view.debounce.value(),
                self.view.live_enabled.isChecked(),
            )
        )

    def _merge_history(self, records: tuple[AuditRecord, ...]) -> None:
        merged = {record.operation_id: record for record in records}
        merged.update(
            (record.operation_id, record) for record in self.view.controller.history
        )
        ordered = sorted(merged.values(), key=lambda record: record.finished_at)
        self.view.controller.history.clear()
        self.view.controller.history.extend(ordered)

    def _runtime_audit(self, audit: object) -> None:
        if not isinstance(audit, RuntimeAudit):
            return
        record = AuditRecord(
            audit.operation_id,
            "runtime-" + audit.runtime,
            "install",
            audit.manager,
            "runtime-" + audit.manager,
            audit.version,
            audit.state.value,
            audit.approved_at,
            audit.finished_at,
            audit.exit_code,
        )
        self.view._merge_history((record,))
        self.view._history_rows()
        if self.view.state_path is not None:
            self.view.state_controller.append_audit((record,))

    def _state_loaded(self, snapshot: object) -> None:
        if not isinstance(snapshot, StoredState):
            return
        self.view.restoring_state = True
        settings = snapshot.settings
        self.view.theme.setCurrentIndex(1 if settings.theme == "dark" else 0)
        self.view.debounce.setValue(settings.search_delay_ms)
        self.view.live_enabled.setChecked(settings.live_search)
        self.view.restoring_state = False
        self.view.state_loading = False
        self.view._settings_enabled(True)
        self.view._merge_history(tuple(reversed(snapshot.history)))
        self.view._history_rows()
        self.view.persistence_note.setText(
            "Settings and output-free history are saved locally."
        )
        self.view._search()

    def _state_saved(self, kind: str) -> None:
        if kind == "clear":
            self.view._clear_session()
            self.view.clearing_history = False
            self.view._busy(self.view.controller.busy)
        self.view.persistence_note.setText(
            "Settings and output-free history are saved locally."
        )

    def _state_error(self, message: str) -> None:
        self.view.state_loading = False
        self.view.clearing_history = False
        self.view.persistence_note.setText("Persistence warning: " + message)
        self.view._settings_enabled(True)
        self.view._busy(self.view.controller.busy)
        if not self.view.pending_close:
            self.view._search()

    def _state_busy(self, busy: bool) -> None:
        if not busy and self.view.pending_close:
            QTimer.singleShot(0, self.view.close)
