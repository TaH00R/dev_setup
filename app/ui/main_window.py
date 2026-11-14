"""Desktop composition, signal wiring, inventory actions and shutdown coordination."""

from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import QTimer, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QMainWindow,
)

from models.catalog import Package
from models.discovery import LivePackage
from models.planning import OperationAction
from services.desktop import (
    DesktopServices,
    DesktopSnapshot,
    InstalledRow,
    UpdateReport,
)
from services.records import PackageRequest

from .components import fill_table, scroll_page
from .environments import EnvironmentPages
from .explore_presenter import ExplorePresenter
from .navigation import PAGE_NAMES, DesktopShell
from .operation_presenter import OperationPresenter
from .pages.dashboard import DashboardPage
from .pages.explore import ExplorePage
from .pages.history import HistoryPage
from .pages.installed import InstalledPage
from .pages.queue import QueuePage
from .pages.settings import SettingsPage
from .pages.updates import UpdatesPage
from .search import SearchController
from .session_presenter import SessionPresenter
from .state import StateController
from .theme import apply_theme
from .workers import Controller


class MainWindow(QMainWindow):
    pages = PAGE_NAMES

    def __init__(
        self,
        services: DesktopServices,
        *,
        auto_refresh: bool = True,
        state_path: Path | None = None,
    ) -> None:
        super().__init__()
        self.services = services
        self.explore_presenter = ExplorePresenter(self)
        self._search = self.explore_presenter._search
        self._search_ready = self.explore_presenter._search_ready
        self._search_busy = self.explore_presenter._search_busy
        self._live_details = self.explore_presenter._live_details
        self._select = self.explore_presenter._select
        self._action_state = self.explore_presenter._action_state
        self._homepage = self.explore_presenter._homepage
        self._preview = self.explore_presenter._preview
        self.operation_presenter = OperationPresenter(self)
        self._review = self.operation_presenter._review
        self._event = self.operation_presenter._event
        self._output = self.operation_presenter._output
        self._show_log = self.operation_presenter._show_log
        self._completed = self.operation_presenter._completed
        self.session_presenter = SessionPresenter(self)
        self._history_rows = self.session_presenter._history_rows
        self._clear = self.session_presenter._clear
        self._clear_session = self.session_presenter._clear_session
        self._settings_enabled = self.session_presenter._settings_enabled
        self._preferences_changed = self.session_presenter._preferences_changed
        self._merge_history = self.session_presenter._merge_history
        self._runtime_audit = self.session_presenter._runtime_audit
        self._state_loaded = self.session_presenter._state_loaded
        self._state_saved = self.session_presenter._state_saved
        self._state_error = self.session_presenter._state_error
        self._state_busy = self.session_presenter._state_busy
        self.state_path = state_path
        self.state_controller = StateController(self)
        self.state_loading = state_path is not None
        self.restoring_state = False
        self.clearing_history = False
        self.state_controller.loaded.connect(self._state_loaded)
        self.state_controller.saved.connect(self._state_saved)
        self.state_controller.error.connect(self._state_error)
        self.state_controller.busy_changed.connect(self._state_busy)
        self.controller = Controller(services, self)
        self.search_controller = SearchController(services.discovery, self)
        self.snapshot: DesktopSnapshot | None = None
        self.update_report: UpdateReport | None = None
        self.package: Package | None = None
        self.matches: list[Package] = []
        self.live_matches: list[LivePackage] = []
        self.live_package: LivePackage | None = None
        self.operation_rows: OrderedDict[str, int] = OrderedDict()
        self.pending_packages: dict[str, str] = {}
        self.log_cache: OrderedDict[str, dict[str, str]] = OrderedDict()
        self.pending_close = False
        self.setWindowTitle("Dev Setup")
        self.resize(1180, 800)
        self.setMinimumSize(940, 650)
        apply_theme()
        self.shell = DesktopShell()
        self.navigation = self.shell.navigation
        self.heading = self.shell.heading
        self.stack = self.shell.stack
        self.status = self.shell.status
        self.refresh_button = self.shell.refresh_button
        self.cancel_button = self.shell.cancel_button
        self.progress = self.shell.progress
        self.dashboard_page = DashboardPage(services)
        self.inventory_count = self.dashboard_page.inventory_count
        self.backend_status = self.dashboard_page.backend_status
        self.dashboard_notes = self.dashboard_page.dashboard_notes
        self.activity = self.dashboard_page.activity
        self.dashboard_page.explore_button.clicked.connect(
            lambda: self.navigation.setCurrentRow(1)
        )
        self.stack.addWidget(self.dashboard_page)
        self.explore_page = ExplorePage(services)
        self.search = self.explore_page.search
        self.category = self.explore_page.category
        self.compatible = self.explore_page.compatible
        self.live_enabled = self.explore_page.live_enabled
        self.refresh_live = self.explore_page.refresh_live
        self.cancel_search = self.explore_page.cancel_search
        self.search_timer = self.explore_page.search_timer
        self.package_list = self.explore_page.package_list
        self.package_name = self.explore_page.package_name
        self.package_details = self.explore_page.package_details
        self.homepage = self.explore_page.homepage
        self.source = self.explore_page.source
        self.action = self.explore_page.action
        self.version = self.explore_page.version
        self.action_note = self.explore_page.action_note
        self.review_button = self.explore_page.review_button
        self.search_summary = self.explore_page.search_summary
        self.live_enabled.toggled.connect(lambda: self._search())
        self.refresh_live.clicked.connect(lambda: self._search(force=True))
        self.cancel_search.clicked.connect(self.search_controller.cancel)
        self.search_timer.timeout.connect(self.search_controller.dispatch)
        self.search.textChanged.connect(lambda: self._search(deferred=True))
        self.category.currentIndexChanged.connect(lambda: self._search())
        self.compatible.toggled.connect(lambda: self._search())
        self.package_list.currentRowChanged.connect(self._select)
        self.homepage.clicked.connect(self._homepage)
        self.source.currentIndexChanged.connect(self._action_state)
        self.action.currentIndexChanged.connect(self._action_state)
        self.review_button.clicked.connect(self._preview)
        self.stack.addWidget(self.explore_page)
        self.installed_page = InstalledPage()
        self.installed_table = self.installed_page.installed_table
        self.installed_note = self.installed_page.installed_note
        self.installed_view = self.installed_page.installed_view
        self.verify_button = self.installed_page.verify_button
        self.remove_button = self.installed_page.remove_button
        self.installed_view.clicked.connect(self._view_installed)
        self.verify_button.clicked.connect(
            lambda: self._installed_request(OperationAction.VERIFY)
        )
        self.remove_button.clicked.connect(
            lambda: self._installed_request(OperationAction.UNINSTALL)
        )
        self.installed_table.itemSelectionChanged.connect(self._selection_buttons)
        self.stack.addWidget(self.installed_page)
        self.updates_page = UpdatesPage()
        self.update_table = self.updates_page.update_table
        self.update_note = self.updates_page.update_note
        self.check_updates = self.updates_page.check_updates
        self.review_update = self.updates_page.review_update
        self.check_updates.clicked.connect(self._check_updates)
        self.review_update.clicked.connect(self._update_request)
        self.update_table.itemSelectionChanged.connect(self._selection_buttons)
        self.stack.addWidget(self.updates_page)
        self.queue_page = QueuePage()
        self.queue_table = self.queue_page.queue_table
        self.log_view = self.queue_page.log_view
        self.queue_table.itemSelectionChanged.connect(self._show_log)
        self.stack.addWidget(self.queue_page)
        self.history_page = HistoryPage(persistent=state_path is not None)
        self.history_note = self.history_page.history_note
        self.history_table = self.history_page.history_table
        self.stack.addWidget(self.history_page)
        self.settings_page = SettingsPage(
            services, persistent=state_path is not None, loading=self.state_loading
        )
        self.theme = self.settings_page.theme
        self.debounce = self.settings_page.debounce
        self.live_search_setting = self.settings_page.live_search_setting
        self.persistence_note = self.settings_page.persistence_note
        self.clear_button = self.settings_page.clear_button
        self.support = self.settings_page.support
        self.theme.currentIndexChanged.connect(lambda index: apply_theme(index == 1))
        self.debounce.valueChanged.connect(self.search_timer.setInterval)
        self.live_search_setting.toggled.connect(self.live_enabled.setChecked)
        self.live_enabled.toggled.connect(self.live_search_setting.setChecked)
        self.clear_button.clicked.connect(self._clear)
        self.support.busy_changed.connect(self._support_busy)
        self.stack.addWidget(scroll_page(self.settings_page))
        self.environment = EnvironmentPages(
            services,
            lambda: not self.controller.busy and not self.clearing_history,
            self,
        )
        self.stack.addWidget(self.environment.presets_page)
        self.stack.addWidget(self.environment.runtimes_page)
        self.environment.package_review.connect(self._review)
        self.environment.runtime_audit.connect(self._runtime_audit)
        self.environment.busy_changed.connect(self._environment_busy)
        self.controller.external_busy = lambda: (
            self.environment.controller.busy or self.clearing_history
        )
        self.setCentralWidget(self.shell)
        self.refresh_button.clicked.connect(self.controller.refresh)
        self.cancel_button.clicked.connect(self._cancel)
        self.navigation.setCurrentRow(0)
        self.controller.busy_changed.connect(self._busy)
        self.controller.snapshot_ready.connect(self._snapshot)
        self.controller.updates_ready.connect(self._updates_ready)
        self.controller.plan_ready.connect(self._review)
        self.controller.execution_ready.connect(self._completed)
        self.controller.progress.connect(self._event)
        self.controller.output.connect(self._output)
        self.controller.error.connect(self._error)
        self.search_controller.ready.connect(self._search_ready)
        self.search_controller.busy_changed.connect(self._search_busy)
        self.search_controller.error.connect(self.search_summary.setText)
        self._search()
        self._selection_buttons()
        self.theme.currentIndexChanged.connect(self._preferences_changed)
        self.debounce.valueChanged.connect(self._preferences_changed)
        self.live_enabled.toggled.connect(self._preferences_changed)
        if state_path is not None:
            self._settings_enabled(False)
            self.state_controller.startup(state_path)
        if auto_refresh:
            QTimer.singleShot(0, self.controller.refresh)

    def _operation_busy(self) -> bool:
        return (
            self.controller.busy
            or self.environment.controller.busy
            or self.clearing_history
        )

    @Slot(bool)
    def _busy(self, busy: bool) -> None:
        busy = busy or self.environment.controller.busy or self.clearing_history
        self.environment.set_external_busy(
            self.controller.busy or self.clearing_history
        )
        self.refresh_button.setEnabled(not busy)
        self.check_updates.setEnabled(not busy)
        self.clear_button.setEnabled(not busy and not self.state_loading)
        self.cancel_button.setVisible(busy)
        self.progress.setVisible(busy)
        self.progress.setRange(0, 0 if busy else 100)
        if busy:
            self.status.setText(
                "Working in the background. You can keep browsing or cancel this task."
            )
        self._action_state()
        self._selection_buttons()
        if not busy and self.pending_close:
            QTimer.singleShot(0, self.close)

    def _cancel(self) -> None:
        self.controller.cancel()
        self.environment.controller.cancel()
        self.status.setText(
            "Cancellation requested. Waiting for the native operation to stop; partial changes may remain."
        )

    @Slot(object)
    def _snapshot(self, snapshot: DesktopSnapshot) -> None:
        self.snapshot = snapshot
        self.support.availability = snapshot.backends
        self.update_report = None
        self.update_table.setRowCount(0)
        self.update_note.setText("Inventory refreshed. Check for updates when ready.")
        self.inventory_count.setText(
            str(len(snapshot.installed))
            if snapshot.inventory.snapshots
            else "Unknown / not supported"
        )
        self.backend_status.setText(
            "\n".join(
                f"{b.backend}: {'available' if b.available else 'not found'}"
                for b in snapshot.backends
            )
            or "No compatible backend"
        )
        notes = [
            f"{backend}: {error.message}"
            for backend, error in snapshot.inventory.errors
        ]
        if not self.services.inspectors:
            notes.append(
                "Native installed-package management is not available for this platform yet. Catalog browsing remains available."
            )
        if snapshot.inventory.detections:
            notes.append(
                f"{sum(d.present for d in snapshot.inventory.detections)} executable observations; these do not prove native package identity or service health."
            )
        self.dashboard_notes.setText(
            "\n".join(notes)
            or "Native inventory refreshed. Service health is not checked."
        )
        fill_table(
            self.installed_table,
            [
                (
                    row.package_id or row.native_id,
                    ", ".join(row.versions) or "Unknown",
                    row.backend,
                    row.source_id or "Unknown (native database does not prove origin)",
                )
                for row in snapshot.installed
            ],
        )
        self.installed_note.setText(
            "\n".join(notes)
            or f"{len(snapshot.installed)} native packages. Select a mapped catalog tool to review actions."
        )
        self.status.setText(
            "Inventory refresh finished."
            if not notes
            else "Inventory refresh finished with limitations; see Dashboard."
        )
        self._selection_buttons()

    def _selected_installed(self) -> InstalledRow | None:
        index = self.installed_table.currentRow()
        if self.snapshot and 0 <= index < len(self.snapshot.installed):
            return self.snapshot.installed[index]
        return None

    def _selection_buttons(self) -> None:
        if not hasattr(self, "review_update"):
            return
        row = self._selected_installed()
        valid = (
            row is not None and row.mapping is not None and row.package_id is not None
        )
        self.installed_view.setEnabled(valid)
        self.verify_button.setEnabled(valid and not self._operation_busy())
        self.remove_button.setEnabled(valid and not self._operation_busy())
        index = self.update_table.currentRow()
        self.review_update.setEnabled(
            self.update_report is not None
            and 0 <= index < len(self.update_report.candidates)
            and not self._operation_busy()
        )

    def _view_installed(self) -> None:
        row = self._selected_installed()
        if row and row.package_id:
            self.search.clear()
            self.category.setCurrentIndex(0)
            self.compatible.setChecked(False)
            self._search()
            self.package_list.setCurrentRow(
                next(n for n, p in enumerate(self.matches) if p.id == row.package_id)
            )
            self.navigation.setCurrentRow(1)

    def _installed_request(self, action: OperationAction) -> None:
        row = self._selected_installed()
        if row and row.package_id and row.mapping:
            self.controller.preview(
                PackageRequest(
                    row.package_id, action, row.backend, row.mapping.source.id
                )
            )

    def _check_updates(self) -> None:
        if self.snapshot is None:
            self.status.setText("Refresh inventory before checking for updates.")
        else:
            self.controller.updates(self.snapshot)

    @Slot(object)
    def _updates_ready(self, report: UpdateReport) -> None:
        self.update_report = report
        fill_table(
            self.update_table,
            [
                (
                    candidate.package_id,
                    ", ".join(candidate.installed),
                    candidate.candidate,
                    candidate.spec.source.name,
                )
                for candidate in report.candidates
            ],
        )
        self.update_note.setText(
            ("Check cancelled; partial results shown.\n" if report.cancelled else "")
            + f"{len(report.candidates)} updates found for mapped installed tools.\n"
            + "\n".join(report.errors)
        )
        self.status.setText("Update check finished. Select an update to review it.")
        self._selection_buttons()

    def _update_request(self) -> None:
        index = self.update_table.currentRow()
        if self.update_report and 0 <= index < len(self.update_report.candidates):
            candidate = self.update_report.candidates[index]
            self.controller.preview(
                PackageRequest(
                    candidate.package_id,
                    OperationAction.UPGRADE,
                    candidate.spec.backend,
                    candidate.spec.source.id,
                )
            )

    @Slot(str)
    def _error(self, message: str) -> None:
        self.status.setText(message)

    def closeEvent(self, event: QCloseEvent) -> None:
        self.pending_close = True
        self.search_timer.stop()
        self.search_controller.cancel()
        if (
            self.controller.busy
            or self.search_controller.busy
            or self.environment.controller.busy
            or self.state_controller.busy
            or self.support.controller.busy
        ):
            self._cancel()
            self.support.controller.cancel()
            event.ignore()
        else:
            self.state_controller.close()
            event.accept()

    @Slot(bool)
    def _environment_busy(self, busy: bool) -> None:
        self._busy(self.controller.busy)

    def _support_busy(self, busy: bool) -> None:
        if not busy and self.pending_close:
            QTimer.singleShot(0, self.close)
