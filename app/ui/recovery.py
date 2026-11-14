"""Explicit inspect/acknowledge workflow; no recovery action constructs a mutation."""

from threading import Event

from PySide6.QtWidgets import (
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from models.lifecycle import LifecycleRecord, aggregate
from runtimes.service import RuntimeService
from services.desktop import DesktopServices
from services.lifecycle import LifecycleStore, RecoveryService

from .components import fill_table, label, table
from .workers import Controller


class RecoveryPanel(QWidget):
    def __init__(
        self,
        services: DesktopServices,
        runtimes: RuntimeService,
        controller: Controller,
    ) -> None:
        super().__init__()
        self.services, self.runtimes, self.controller = services, runtimes, controller
        self.store: LifecycleStore | None = None
        self.records: tuple[LifecycleRecord, ...] = ()
        self.all_records: tuple[LifecycleRecord, ...] = ()
        self.busy = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(
            label(
                "Unfinished or uncertain native work requires inspection",
                "sectionTitle",
            )
        )
        self.note = label(
            "Outcome unknown. Native changes may already have occurred. Inspect current inventory before a fresh review; no operation will be rerun automatically."
        )
        layout.addWidget(self.note)
        self.table = table(
            (
                "Package / action",
                "Backend",
                "Last phase",
                "Outcome",
                "Current observation",
                "Plan outcome",
            )
        )
        self.table.setAccessibleName("Interrupted operations")
        self.table.setMaximumHeight(160)
        self.table.itemSelectionChanged.connect(self._buttons)
        layout.addWidget(self.table)
        self.details = label("")
        self.details.setAccessibleName("Interrupted operation source and inspection")
        layout.addWidget(self.details)
        actions = QHBoxLayout()
        self.inspect_button = QPushButton("Inspect current native state")
        self.inspect_button.clicked.connect(self.inspect)
        self.acknowledge = QPushButton("Acknowledge inspected outcome")
        self.acknowledge.clicked.connect(self.reconcile)
        actions.addWidget(self.inspect_button)
        actions.addWidget(self.acknowledge)
        actions.addStretch()
        layout.addLayout(actions)
        controller.recovery_ready.connect(self._ready)
        controller.busy_changed.connect(self.set_busy)
        self.hide()

    def configure(
        self, store: LifecycleStore, records: tuple[LifecycleRecord, ...]
    ) -> None:
        self.store = store
        self._ready(records)

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        self._buttons()

    def add_records(self, value: object) -> None:
        if (
            not isinstance(value, tuple)
            or not value
            or any(not isinstance(record, LifecycleRecord) for record in value)
        ):
            return
        merged = {record.operation_id: record for record in self.all_records}
        merged.update((record.operation_id, record) for record in value)
        self._ready(tuple(merged.values()))

    def clear_resolved(self) -> None:
        protected = {
            record.execution_id
            for record in self.all_records
            if record.needs_recovery or not record.state.terminal
        }
        self._ready(
            tuple(
                record
                for record in self.all_records
                if record.execution_id in protected
            )
        )

    def _selected(self) -> LifecycleRecord | None:
        index = self.table.currentRow()
        return self.records[index] if 0 <= index < len(self.records) else None

    def _buttons(self) -> None:
        record = self._selected()
        observations = {
            "unknown": "Not inspected yet.",
            "matches_expected": "Current inventory matches the reviewed target.",
            "matches_before": "Current inventory matches the reviewed prior state.",
            "changed": "Current inventory differs from the reviewed states.",
            "unavailable": "Native inventory could not be inspected on this host/backend.",
        }
        self.details.setText(
            f"Native ID: {record.native_id} | Source: {record.source_id}\n{observations.get(record.verification, record.verification)} Original execution outcome: {record.state.value}; no rollback is implied."
            if record
            else "Select interrupted work to inspect its source and current state."
        )
        enabled = record is not None and record.needs_recovery and not self.busy
        self.inspect_button.setEnabled(enabled)
        self.acknowledge.setEnabled(
            bool(
                enabled
                and record
                and record.inspected_at is not None
                and record.verification not in {"unknown", "unavailable"}
            )
        )

    def _ready(self, value: object) -> None:
        if not isinstance(value, tuple) or any(
            not isinstance(item, LifecycleRecord) for item in value
        ):
            return
        self.all_records = value
        self.records = tuple(
            record
            for record in value
            if record.needs_recovery
            or record.reconciled_at is not None
            or record.state.value == "interrupted"
        )
        fill_table(
            self.table,
            [
                (
                    f"{record.package_id} / {record.action}",
                    record.backend,
                    record.phase.value,
                    record.state.value + " / acknowledged"
                    if record.reconciled_at
                    else record.state.value + " / inspection required",
                    record.verification,
                    aggregate(
                        tuple(
                            item.state.value
                            for item in self.all_records
                            if item.execution_id == record.execution_id
                        )
                    ).value,
                )
                for record in self.records
            ],
        )
        self.setVisible(bool(self.records))
        if self.records:
            self.table.selectRow(0)
        self._buttons()

    def inspect(self) -> None:
        record = self._selected()
        if record is None or not record.needs_recovery or self.store is None:
            return
        store = self.store

        def task(
            cancel: Event, event: object, output: object
        ) -> tuple[LifecycleRecord, ...]:
            service = RecoveryService(
                store, self.services.host, self.services.inspectors, self.runtimes
            )
            service.inspect(record, cancel)
            return store.load_lifecycle()

        self.controller.start("recovery", task)

    def reconcile(self) -> None:
        record = self._selected()
        if record is None or self.store is None or not self.acknowledge.isEnabled():
            return
        if (
            QMessageBox.question(
                self,
                "Acknowledge current state?",
                "Acknowledge this inventory observation and close recovery for this operation? This does not prove what the previous process changed, undo changes, or rerun anything.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        store = self.store

        def task(
            cancel: Event, event: object, output: object
        ) -> tuple[LifecycleRecord, ...]:
            if not cancel.is_set():
                RecoveryService(
                    store, self.services.host, self.services.inspectors, self.runtimes
                ).reconcile(record, confirmed=True)
            return store.load_lifecycle()

        self.controller.start("recovery", task)
