"""UpdatesPage controls, independent of the main window."""

from PySide6.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..components import label, table


class UpdatesPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        self.setObjectName("page")
        layout.setContentsMargins(0, 0, 0, 0)
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
        buttons.addWidget(self.check_updates)
        self.review_update = QPushButton("Review selected update")
        buttons.addWidget(self.review_update)
        buttons.addStretch()
        layout.addLayout(buttons)
        self.review_update.setEnabled(False)
