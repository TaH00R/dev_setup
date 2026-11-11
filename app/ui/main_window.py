"""Thin desktop widgets: render service state and send explicit user intent."""

from collections import OrderedDict

from PySide6.QtCore import Qt, QTimer, QUrl, Slot
from PySide6.QtGui import QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from models.catalog import Package
from models.operations import OperationState, ProgressEvent
from models.package import PackageVersion
from models.planning import OperationAction
from models.specs import InstallSpec
from services.desktop import (
    DesktopServices,
    DesktopSnapshot,
    InstalledRow,
    UpdateReport,
)
from services.records import ExecutionReport, PackageRequest, ReviewedPlan
from system.commands import OutputSnapshot

from .review import ReviewDialog
from .theme import apply_theme
from .workers import Controller


def label(text: str, name: str = "") -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    widget.setObjectName(name)
    return widget


def table(headers: tuple[str, ...]) -> QTableWidget:
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.verticalHeader().hide()
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    widget.setWordWrap(False)
    return widget


def fill_table(widget: QTableWidget, rows: list[tuple[str, ...]]) -> None:
    widget.setRowCount(len(rows))
    for row, values in enumerate(rows):
        for column, value in enumerate(values):
            widget.setItem(row, column, QTableWidgetItem(value))


class MainWindow(QMainWindow):
    pages = (
        "Dashboard",
        "Explore",
        "Installed",
        "Updates",
        "Queue",
        "History",
        "Settings",
    )

    def __init__(self, services: DesktopServices, *, auto_refresh: bool = True) -> None:
        super().__init__()
        self.services = services
        self.controller = Controller(services, self)
        self.snapshot: DesktopSnapshot | None = None
        self.update_report: UpdateReport | None = None
        self.package: Package | None = None
        self.matches: list[Package] = []
        self.operation_rows: OrderedDict[str, int] = OrderedDict()
        self.pending_packages: dict[str, str] = {}
        self.log_cache: OrderedDict[str, dict[str, str]] = OrderedDict()
        self.pending_close = False
        self.setWindowTitle("Dev Setup")
        self.resize(1180, 800)
        self.setMinimumSize(940, 650)
        apply_theme()
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(190)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 18, 18, 18)
        side.addWidget(label("Dev Setup", "brand"))
        side.addWidget(label("Your development toolbox"))
        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.addItems(self.pages)
        side.addWidget(self.navigation)
        side.addWidget(label("Curated sources\nReview every change"))
        root.addWidget(sidebar)
        content = QVBoxLayout()
        content.setContentsMargins(26, 22, 26, 16)
        self.heading = label("Dashboard", "pageTitle")
        content.addWidget(self.heading)
        self.stack = QStackedWidget()
        content.addWidget(self.stack, 1)
        self._dashboard()
        self._explore()
        self._installed()
        self._updates()
        self._queue()
        self._history()
        self._settings()
        footer = QHBoxLayout()
        self.status = label(
            "Ready. Refresh inventory to inspect installed packages.", "muted"
        )
        footer.addWidget(self.status, 1)
        self.refresh_button = QPushButton("Refresh inventory")
        self.refresh_button.clicked.connect(self.controller.refresh)
        footer.addWidget(self.refresh_button)
        self.cancel_button = QPushButton("Cancel current task")
        self.cancel_button.clicked.connect(self._cancel)
        self.cancel_button.hide()
        footer.addWidget(self.cancel_button)
        content.addLayout(footer)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.hide()
        content.addWidget(self.progress)
        root.addLayout(content, 1)
        self.setCentralWidget(central)
        self.navigation.currentRowChanged.connect(self._navigate)
        self.navigation.setCurrentRow(0)
        self.controller.busy_changed.connect(self._busy)
        self.controller.snapshot_ready.connect(self._snapshot)
        self.controller.updates_ready.connect(self._updates_ready)
        self.controller.plan_ready.connect(self._review)
        self.controller.execution_ready.connect(self._completed)
        self.controller.progress.connect(self._event)
        self.controller.output.connect(self._output)
        self.controller.error.connect(self._error)
        self._search()
        self._selection_buttons()
        if auto_refresh:
            QTimer.singleShot(0, self.controller.refresh)

    def _page(self) -> QVBoxLayout:
        widget = QWidget()
        widget.setObjectName("page")
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        self.stack.addWidget(widget)
        return layout

    def _dashboard(self) -> None:
        layout = self._page()
        host = self.services.host
        distro = (
            f" · {host.distribution.name or host.distribution.id} {host.distribution.version or ''}"
            if host.distribution
            else ""
        )
        layout.addWidget(
            label(
                f"{host.os.value.title()} · {host.architecture.value}{distro}",
                "sectionTitle",
            )
        )
        layout.addWidget(
            label(
                "Find the tools you need, review their source, and keep track of every operation.",
                "muted",
            )
        )
        stats = QGroupBox("Your workspace")
        form = QFormLayout(stats)
        form.addRow("Curated tools", label(str(len(self.services.catalog.packages))))
        self.inventory_count = label("Not checked")
        form.addRow("Native installed packages", self.inventory_count)
        self.backend_status = label("Not checked")
        form.addRow("Package managers", self.backend_status)
        layout.addWidget(stats)
        self.dashboard_notes = label(
            "Inventory is read-only. No package is installed or upgraded during refresh.",
            "muted",
        )
        layout.addWidget(self.dashboard_notes)
        activity = QGroupBox("Recent activity")
        activity_layout = QVBoxLayout(activity)
        self.activity = label("No operations in this session.")
        activity_layout.addWidget(self.activity)
        layout.addWidget(activity)
        explore = QPushButton("Explore the catalog")
        explore.setObjectName("primary")
        explore.clicked.connect(lambda: self.navigation.setCurrentRow(1))
        layout.addWidget(explore, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()

    def _explore(self) -> None:
        layout = self._page()
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search tools, languages, or package IDs")
        filters.addWidget(self.search, 1)
        self.category = QComboBox()
        self.category.addItem("All categories", None)
        for category in self.services.catalog.categories:
            self.category.addItem(category.replace("-", " ").title(), category)
        filters.addWidget(self.category)
        self.compatible = QCheckBox("For this platform")
        filters.addWidget(self.compatible)
        layout.addLayout(filters)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(250)
        self.search_timer.timeout.connect(self._search)
        self.search.textChanged.connect(lambda: self.search_timer.start())
        self.category.currentIndexChanged.connect(self._search)
        self.compatible.toggled.connect(self._search)
        split = QSplitter()
        self.package_list = QListWidget()
        self.package_list.setWordWrap(True)
        self.package_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.package_list.currentRowChanged.connect(self._select)
        split.addWidget(self.package_list)
        details = QGroupBox("Package details")
        detail = QVBoxLayout(details)
        self.package_name = label("Choose a package", "sectionTitle")
        detail.addWidget(self.package_name)
        self.package_details = QPlainTextEdit()
        self.package_details.setReadOnly(True)
        detail.addWidget(self.package_details, 1)
        self.homepage = QPushButton("Open official homepage")
        self.homepage.clicked.connect(self._homepage)
        detail.addWidget(self.homepage)
        form = QFormLayout()
        self.source = QComboBox()
        self.source.currentIndexChanged.connect(self._action_state)
        form.addRow("Source", self.source)
        self.action = QComboBox()
        for action in OperationAction:
            self.action.addItem(action.value.title(), action)
        self.action.currentIndexChanged.connect(self._action_state)
        form.addRow("Action", self.action)
        self.version = QLineEdit()
        self.version.setPlaceholderText(
            "Native default (exact version where supported)"
        )
        form.addRow("Version", self.version)
        detail.addLayout(form)
        self.action_note = label("", "muted")
        detail.addWidget(self.action_note)
        self.review_button = QPushButton("Review operation")
        self.review_button.setObjectName("primary")
        self.review_button.clicked.connect(self._preview)
        detail.addWidget(self.review_button)
        split.addWidget(details)
        split.setSizes([440, 480])
        layout.addWidget(split, 1)
        self.search_summary = label("", "muted")
        layout.addWidget(self.search_summary)

    def _installed(self) -> None:
        layout = self._page()
        layout.addWidget(
            label(
                "Native inventory is separate from executable detection and service health.",
                "muted",
            )
        )
        self.installed_table = table(
            ("Package", "Version", "Backend", "Installed source")
        )
        layout.addWidget(self.installed_table)
        self.installed_note = label(
            "Refresh inventory to load installed packages.", "muted"
        )
        layout.addWidget(self.installed_note)
        buttons = QHBoxLayout()
        self.installed_view = QPushButton("View package")
        self.installed_view.clicked.connect(self._view_installed)
        buttons.addWidget(self.installed_view)
        self.verify_button = QPushButton("Review verification")
        self.verify_button.clicked.connect(
            lambda: self._installed_request(OperationAction.VERIFY)
        )
        buttons.addWidget(self.verify_button)
        self.remove_button = QPushButton("Review removal")
        self.remove_button.clicked.connect(
            lambda: self._installed_request(OperationAction.UNINSTALL)
        )
        buttons.addWidget(self.remove_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.installed_table.itemSelectionChanged.connect(self._selection_buttons)
        self._selection_buttons()

    def _updates(self) -> None:
        layout = self._page()
        layout.addWidget(
            label(
                "Check installed catalog tools against their native candidates. Every update requires a fresh review.",
                "muted",
            )
        )
        self.update_table = table(("Package", "Installed", "Candidate", "Source"))
        layout.addWidget(self.update_table)
        self.update_note = label("Refresh inventory, then check for updates.", "muted")
        layout.addWidget(self.update_note)
        buttons = QHBoxLayout()
        self.check_updates = QPushButton("Check for updates")
        self.check_updates.clicked.connect(self._check_updates)
        buttons.addWidget(self.check_updates)
        self.review_update = QPushButton("Review selected update")
        self.review_update.clicked.connect(self._update_request)
        buttons.addWidget(self.review_update)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.update_table.itemSelectionChanged.connect(self._selection_buttons)
        self.review_update.setEnabled(False)

    def _queue(self) -> None:
        layout = self._page()
        layout.addWidget(
            label(
                "One plan runs at a time. Logs stay in this session and are bounded per operation.",
                "muted",
            )
        )
        self.queue_table = table(("Package", "State", "Progress", "Result"))
        self.queue_table.itemSelectionChanged.connect(self._show_log)
        layout.addWidget(self.queue_table, 1)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Select an operation to inspect its output.")
        layout.addWidget(self.log_view, 1)

    def _history(self) -> None:
        layout = self._page()
        layout.addWidget(
            label(
                "Session history contains approval and result metadata. It is not saved after closing the app.",
                "muted",
            )
        )
        self.history_table = table(
            ("Package / action", "Source", "Version", "Result", "Finished (UTC)")
        )
        layout.addWidget(self.history_table)

    def _settings(self) -> None:
        layout = self._page()
        appearance = QGroupBox("Appearance and search")
        form = QFormLayout(appearance)
        self.theme = QComboBox()
        self.theme.addItems(("Light", "Dark"))
        self.theme.currentIndexChanged.connect(lambda index: apply_theme(index == 1))
        form.addRow("Theme", self.theme)
        self.debounce = QSpinBox()
        self.debounce.setRange(150, 1000)
        self.debounce.setValue(250)
        self.debounce.setSuffix(" ms")
        self.debounce.valueChanged.connect(self.search_timer.setInterval)
        form.addRow("Search delay", self.debounce)
        form.addRow("Concurrency", label("One plan at a time"))
        layout.addWidget(appearance)
        sources = QGroupBox("Package sources")
        source_layout = QVBoxLayout(sources)
        source_layout.addWidget(
            label(
                "Choose a reviewed source in package details. Adding native repositories is not available here.",
                "muted",
            )
        )
        source_layout.addWidget(
            label(
                "\n".join(
                    sorted(
                        {
                            f"{spec.source.name}: {spec.source.url or 'URL unavailable'}"
                            for p in self.services.catalog.packages
                            for spec in p.install_specs
                            if spec.support.supports(self.services.host)
                        }
                    )
                )
                or "No reviewed source mappings for this platform."
            )
        )
        layout.addWidget(sources)
        privacy = QGroupBox("Session privacy")
        privacy_layout = QVBoxLayout(privacy)
        privacy_layout.addWidget(
            label(
                "Settings and history are kept in memory. Native administrator prompts handle passwords. No telemetry is collected."
            )
        )
        self.clear_button = QPushButton("Clear session history and logs")
        self.clear_button.clicked.connect(self._clear)
        privacy_layout.addWidget(
            self.clear_button, alignment=Qt.AlignmentFlag.AlignLeft
        )
        layout.addWidget(privacy)
        layout.addStretch()

    @Slot(int)
    def _navigate(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self.heading.setText(self.pages[index])

    def _search(self) -> None:
        matches = self.services.catalog.search(
            self.search.text(),
            category=self.category.currentData(),
            target=self.services.host,
            mapped_only=self.compatible.isChecked(),
        )
        selected_id = self.package.id if self.package else None
        self.matches = [match.package for match in matches]
        self.package_list.clear()
        for package in self.matches:
            mapped = any(
                s.support.supports(self.services.host) for s in package.install_specs
            )
            item = QListWidgetItem(
                f"{package.name}\n{package.summary}\n{package.category.replace('-', ' ')} · Curated · {'Platform mapping available' if mapped else 'Unavailable on this platform'}"
            )
            self.package_list.addItem(item)
        self.search_summary.setText(
            f"{len(matches)} curated tools · Local metadata · Live search comes in a later release"
        )
        index = next((n for n, p in enumerate(self.matches) if p.id == selected_id), 0)
        self.package_list.setCurrentRow(index if self.matches else -1)
        if not self.matches:
            self._select(-1)

    @Slot(int)
    def _select(self, index: int) -> None:
        self.package = self.matches[index] if 0 <= index < len(self.matches) else None
        self.source.clear()
        self.version.clear()
        package = self.package
        self.package_name.setText(package.name if package else "No package selected")
        self.homepage.setEnabled(package is not None)
        if package:
            for spec in package.install_specs:
                if spec.support.supports(self.services.host):
                    self.source.addItem(
                        f"{spec.source.name} / {spec.backend} / {spec.native_id}", spec
                    )
        self._action_state()

    def _action_state(self) -> None:
        spec = self.source.currentData()
        action = OperationAction(self.action.currentData())
        package = self.package
        lines = []
        if package:
            lines.extend(
                [
                    package.summary,
                    "",
                    f"Homepage: {package.homepage}",
                    f"Documentation: {package.documentation or 'Not listed'}",
                    f"License: {package.license or 'Not listed'}",
                    f"Vendor: {package.vendor or 'Not listed'}",
                    f"Category: {package.category}",
                    f"Dependencies: {', '.join(d.package_id for d in package.dependencies) or 'None declared'}",
                    f"Conflicts: {', '.join(package.conflicts) or 'None declared'}",
                ]
            )
        reason = "No reviewed mapping for this OS, architecture, or distribution."
        if isinstance(spec, InstallSpec) and isinstance(action, OperationAction):
            reason = (
                self.services.action_reason(spec, action)
                or "A fresh plan will show the exact operation before approval."
            )
            lines.extend(
                [
                    "",
                    f"Source: {spec.source.name} ({spec.source.kind.value}; {spec.source.trust.value})",
                    f"Source URL: {spec.source.url or 'Not listed'}",
                    f"Native ID: {spec.native_id}",
                    f"Platform: {spec.support.os.value}; architectures: {', '.join(a.value for a in spec.support.architectures)}",
                    f"Privileges: {spec.privilege.value}",
                    f"Estimated size: {str(spec.estimated_size_bytes) + ' bytes' if spec.estimated_size_bytes is not None else 'Unknown'}",
                    f"Restart required: {'Yes' if spec.restart_required else 'Not declared'}",
                    *spec.notes,
                ]
            )
            can_select_version = self.services.version_selection(spec, action)
            self.version.setEnabled(can_select_version and not self.controller.busy)
            if not can_select_version:
                self.version.clear()
            self.review_button.setEnabled(
                self.services.action_reason(spec, action) is None
                and not self.controller.busy
            )
        else:
            self.review_button.setEnabled(False)
            self.version.setEnabled(False)
        self.package_details.setPlainText("\n".join(lines))
        self.action_note.setText(reason)

    def _homepage(self) -> None:
        if self.package:
            QDesktopServices.openUrl(QUrl(self.package.homepage))

    def _preview(self) -> None:
        spec = self.source.currentData()
        if self.package and isinstance(spec, InstallSpec):
            try:
                version = (
                    PackageVersion(self.version.text().strip())
                    if self.version.isEnabled() and self.version.text().strip()
                    else None
                )
                request = PackageRequest(
                    self.package.id,
                    OperationAction(self.action.currentData()),
                    spec.backend,
                    spec.source.id,
                    version,
                )
            except ValueError:
                self._error("Enter a valid exact native version.")
                return
            self.controller.preview(request)

    @Slot(bool)
    def _busy(self, busy: bool) -> None:
        self.refresh_button.setEnabled(not busy)
        self.check_updates.setEnabled(not busy)
        self.clear_button.setEnabled(not busy)
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
        self.status.setText(
            "Cancellation requested. Waiting for the native operation to stop; partial changes may remain."
        )

    @Slot(object)
    def _snapshot(self, snapshot: DesktopSnapshot) -> None:
        self.snapshot = snapshot
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
        self.verify_button.setEnabled(valid and not self.controller.busy)
        self.remove_button.setEnabled(valid and not self.controller.busy)
        index = self.update_table.currentRow()
        self.review_update.setEnabled(
            self.update_report is not None
            and 0 <= index < len(self.update_report.candidates)
            and not self.controller.busy
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

    @Slot(object)
    def _review(self, reviewed: ReviewedPlan) -> None:
        if self.pending_close:
            return
        dialog = ReviewDialog(reviewed, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and reviewed.plan.ready:
            consent = dialog.consent()
            if reviewed.plan.requires_consent and consent is None:
                return
            self.navigation.setCurrentRow(4)
            if self.controller.execute(reviewed, consent):
                for item in reviewed.plan.items:
                    placeholder = f"pending-{reviewed.plan.id}-{item.package_id}"
                    self._event(
                        ProgressEvent(
                            placeholder,
                            item.package_id,
                            OperationState.QUEUED,
                            "Waiting for this plan's preceding operations.",
                        )
                    )
                    self.pending_packages[item.package_id] = placeholder
        else:
            self.status.setText("Plan review closed. No package changes were started.")
        dialog.deleteLater()

    @Slot(object)
    def _event(self, event: ProgressEvent) -> None:
        placeholder = self.pending_packages.pop(event.package_id, None)
        if placeholder in self.operation_rows:
            self.operation_rows = OrderedDict(
                (event.operation_id if key == placeholder else key, row)
                for key, row in self.operation_rows.items()
            )
        if event.operation_id not in self.operation_rows:
            if len(self.operation_rows) >= 100:
                self.queue_table.removeRow(0)
                self.operation_rows.popitem(last=False)
                self.operation_rows = OrderedDict(
                    (key, value - 1) for key, value in self.operation_rows.items()
                )
            self.operation_rows[event.operation_id] = self.queue_table.rowCount()
            self.queue_table.insertRow(self.queue_table.rowCount())
        row = self.operation_rows[event.operation_id]
        for column, value in enumerate(
            (
                event.package_id,
                event.state.value,
                f"{event.percent:g}%" if event.percent is not None else "—",
                event.message,
            )
        ):
            self.queue_table.setItem(row, column, QTableWidgetItem(value))
        if self.queue_table.currentRow() < 0:
            self.queue_table.selectRow(row)
        self.status.setText(f"{event.package_id}: {event.message}")
        self._show_log()

    @Slot(str, object)
    def _output(self, operation_id: str, snapshot: OutputSnapshot) -> None:
        streams = self.log_cache.setdefault(operation_id, {})
        streams[snapshot.stream] = snapshot.text[-65536:] + (
            "\n[Output truncated]" if snapshot.truncated else ""
        )
        while len(self.log_cache) > 100:
            self.log_cache.popitem(last=False)
        self._show_log()

    def _show_log(self) -> None:
        index = self.queue_table.currentRow()
        operation_id = next(
            (key for key, row in self.operation_rows.items() if row == index), None
        )
        streams = self.log_cache.get(operation_id, {}) if operation_id else {}
        self.log_view.setPlainText(
            "\n\n".join(f"{stream.upper()}\n{text}" for stream, text in streams.items())
        )

    @Slot(object)
    def _completed(self, report: ExecutionReport) -> None:
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
                self._event(
                    ProgressEvent(
                        operation.id,
                        operation.item.package_id,
                        operation.state,
                        message,
                    )
                )
                self._output(
                    operation.id,
                    OutputSnapshot("stdout", result.stdout, result.output_truncated),
                )
                self._output(
                    operation.id,
                    OutputSnapshot("stderr", result.stderr, result.output_truncated),
                )
        self._history_rows()
        self.status.setText(
            "Plan finished. Review results in Queue; refresh inventory to inspect current state."
        )

    def _history_rows(self) -> None:
        rows = list(reversed(self.controller.history))
        fill_table(
            self.history_table,
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
        self.activity.setText(
            "\n".join(
                f"{row.package_id} · {row.action} · {row.state}" for row in rows[:5]
            )
            or "No operations in this session."
        )

    def _clear(self) -> None:
        self.controller.history.clear()
        self.operation_rows.clear()
        self.pending_packages.clear()
        self.log_cache.clear()
        self.queue_table.setRowCount(0)
        self.log_view.clear()
        self._history_rows()
        self.status.setText("Session history and logs cleared.")

    @Slot(str)
    def _error(self, message: str) -> None:
        self.status.setText(message)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.controller.busy:
            self.pending_close = True
            self._cancel()
            event.ignore()
        else:
            event.accept()
