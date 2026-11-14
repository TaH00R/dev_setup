"""InstalledPage controls, independent of the main window."""

from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..components import label, table


class InstalledPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        self.setObjectName("page")
        layout.setContentsMargins(0, 0, 0, 0)
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
        buttons.addWidget(self.installed_view)
        self.verify_button = QPushButton("Review verification")
        buttons.addWidget(self.verify_button)
        self.remove_button = QPushButton("Review removal")
        buttons.addWidget(self.remove_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.installed_view.setEnabled(False)
        self.verify_button.setEnabled(False)
        self.remove_button.setEnabled(False)
