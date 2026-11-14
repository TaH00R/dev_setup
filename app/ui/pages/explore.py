"""ExplorePage controls, independent of the main window."""

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from models.planning import OperationAction
from services.desktop import (
    DesktopServices,
)

from ..components import label


class ExplorePage(QWidget):
    def __init__(self, services: DesktopServices) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        self.setObjectName("page")
        layout.setContentsMargins(0, 0, 0, 0)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setAccessibleName("Search packages")
        self.search.setPlaceholderText("Search tools, languages, or package IDs")
        filters.addWidget(self.search, 1)
        self.category = QComboBox()
        self.category.setAccessibleName("Package category")
        self.category.addItem("All categories", None)
        for category in services.catalog.categories:
            self.category.addItem(category.replace("-", " ").title(), category)
        filters.addWidget(self.category)
        self.compatible = QCheckBox("Curated for this platform")
        filters.addWidget(self.compatible)
        layout.addLayout(filters)
        live_controls = QHBoxLayout()
        self.live_enabled = QCheckBox("Include live sources")
        self.live_enabled.setChecked(True)
        self.live_enabled.setToolTip(
            "Use detected package managers. Queries may use the network and refresh native metadata caches."
        )
        live_controls.addWidget(self.live_enabled)
        self.refresh_live = QPushButton("Refresh live results")
        live_controls.addWidget(self.refresh_live)
        self.cancel_search = QPushButton("Cancel search")
        self.cancel_search.hide()
        live_controls.addWidget(self.cancel_search)
        live_controls.addStretch()
        layout.addLayout(live_controls)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(250)
        split = QSplitter()
        self.package_list = QListWidget()
        self.package_list.setAccessibleName("Package search results")
        self.package_list.setWordWrap(True)
        self.package_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        split.addWidget(self.package_list)
        details = QGroupBox("Package details")
        detail = QVBoxLayout(details)
        self.package_name = label("Choose a package", "sectionTitle")
        detail.addWidget(self.package_name)
        self.package_details = QPlainTextEdit()
        self.package_details.setAccessibleName("Package details")
        self.package_details.setReadOnly(True)
        detail.addWidget(self.package_details, 1)
        self.homepage = QPushButton("Open official homepage")
        detail.addWidget(self.homepage)
        form = QFormLayout()
        self.source = QComboBox()
        form.addRow("Source", self.source)
        self.action = QComboBox()
        for action in OperationAction:
            self.action.addItem(action.value.title(), action)
        form.addRow("Action", self.action)
        self.version = QLineEdit()
        self.version.setAccessibleName("Exact package version")
        self.version.setPlaceholderText(
            "Native default (exact version where supported)"
        )
        form.addRow("Version", self.version)
        detail.addLayout(form)
        self.action_note = label("", "muted")
        detail.addWidget(self.action_note)
        self.review_button = QPushButton("Review operation")
        self.review_button.setObjectName("primary")
        detail.addWidget(self.review_button)
        split.addWidget(details)
        split.setSizes([440, 480])
        layout.addWidget(split, 1)
        self.search_summary = label("", "muted")
        layout.addWidget(self.search_summary)
