"""QueuePage controls, independent of the main window."""

from PySide6.QtWidgets import (
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..components import label, table


class QueuePage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        self.setObjectName("page")
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(
            label(
                "One plan runs at a time. Logs stay in this session and are bounded per operation.",
                "muted",
            )
        )
        self.queue_table = table(("Package", "State", "Progress", "Result"))
        layout.addWidget(self.queue_table, 1)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setPlaceholderText("Select an operation to inspect its output.")
        layout.addWidget(self.log_view, 1)
