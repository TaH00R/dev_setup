"""Plain-text runtime and project reviews requiring explicit approval."""

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from .components import text_label


class ChangeReview(QDialog):
    """A review is accepted only by an explicit enabled action, never by default."""

    def __init__(
        self,
        title: str,
        details: str,
        *,
        required: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 600)
        layout = QVBoxLayout(self)
        layout.addWidget(text_label(title))
        self.details = QPlainTextEdit()
        self.details.setAccessibleName("Exact changes to review")
        self.details.setReadOnly(True)
        self.details.setPlainText(details)
        layout.addWidget(self.details)
        self.approve = QCheckBox("I approve the exact changes shown above.")
        self.approve.setVisible(required)
        layout.addWidget(self.approve)
        layout.addWidget(
            text_label(
                "Cancellation is best effort. Native changes are not rolled back. Project writes may finish before cancellation."
            )
        )
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.run_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.run_button.setText(
            "Approve and apply" if required else "Confirm reviewed result"
        )
        self.run_button.setAutoDefault(False)
        self.run_button.setDefault(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        self.run_button.setEnabled(not required)
        self.approve.toggled.connect(
            lambda checked: self.run_button.setEnabled(checked or not required)
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
