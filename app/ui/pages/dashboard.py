"""DashboardPage controls, independent of the main window."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from services.desktop import (
    DesktopServices,
)

from ..components import label


class DashboardPage(QWidget):
    def __init__(self, services: DesktopServices) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        self.setObjectName("page")
        layout.setContentsMargins(0, 0, 0, 0)
        host = services.host
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
        form.addRow("Curated tools", label(str(len(services.catalog.packages))))
        self.inventory_count = label("Not checked")
        form.addRow("Native installed packages", self.inventory_count)
        self.backend_status = label("Not checked")
        form.addRow("Package managers", self.backend_status)
        form.addRow(
            "Package changes",
            label(
                "Awaiting native qualification; search and inventory remain available."
            ),
        )
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
        self.explore_button = QPushButton("Explore the catalog")
        self.explore_button.setObjectName("primary")
        layout.addWidget(self.explore_button, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
