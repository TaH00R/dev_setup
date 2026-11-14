"""Desktop navigation and task footer, independent of service workflows."""

from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QListWidget,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .components import label

PAGE_NAMES = (
    "Dashboard",
    "Explore",
    "Installed",
    "Updates",
    "Queue",
    "History",
    "Settings",
    "Presets",
    "Runtimes",
)


class DesktopShell(QWidget):
    def __init__(self, pages: tuple[str, ...] = PAGE_NAMES) -> None:
        super().__init__()
        central = self
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
        self.navigation.addItems(pages)
        side.addWidget(self.navigation)
        side.addWidget(label("Curated sources\nReview every change"))
        root.addWidget(sidebar)
        content = QVBoxLayout()
        content.setContentsMargins(26, 22, 26, 16)
        self.heading = label("Dashboard", "pageTitle")
        content.addWidget(self.heading)
        self.stack = QStackedWidget()
        content.addWidget(self.stack, 1)
        footer = QHBoxLayout()
        self.status = label(
            "Ready. Refresh inventory to inspect installed packages.", "muted"
        )
        footer.addWidget(self.status, 1)
        self.refresh_button = QPushButton("Refresh inventory")
        footer.addWidget(self.refresh_button)
        self.cancel_button = QPushButton("Cancel current task")
        self.cancel_button.hide()
        footer.addWidget(self.cancel_button)
        content.addLayout(footer)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.hide()
        content.addWidget(self.progress)
        root.addLayout(content, 1)
        self.navigation.setAccessibleName("Pages")
        self.refresh_button.setAccessibleName("Refresh inventory")
        self.cancel_button.setAccessibleName("Cancel current task")
        self.navigation.currentRowChanged.connect(self.navigate)
        self.navigation.setCurrentRow(0)

    def navigate(self, index: int) -> None:
        if 0 <= index < self.navigation.count():
            self.stack.setCurrentIndex(index)
            self.heading.setText(self.navigation.item(index).text())
