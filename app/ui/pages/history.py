"""HistoryPage controls, independent of the main window."""

from PySide6.QtWidgets import (
    QVBoxLayout,
    QWidget,
)

from ..components import label, table


class HistoryPage(QWidget):
    def __init__(
        self,
        *,
        persistent: bool = False,
    ) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        self.setObjectName("page")
        layout.setContentsMargins(0, 0, 0, 0)
        self.history_note = label(
            "History saves approval and result metadata locally; process output stays in this session."
            if persistent
            else "Session history contains approval and result metadata. It is not saved after closing the app.",
            "muted",
        )
        layout.addWidget(self.history_note)
        self.history_table = table(
            ("Package / action", "Source", "Version", "Result", "Finished (UTC)")
        )
        layout.addWidget(self.history_table)
