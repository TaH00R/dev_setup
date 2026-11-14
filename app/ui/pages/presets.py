"""PresetsPage controls, independent of the main window."""

from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from services.environments import (
    starter_presets,
)

from ..components import text_label


class PresetsPage(QWidget):
    def __init__(self) -> None:
        super().__init__()
        page = self
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(
            text_label(
                "Choose a starter bundle or build a collection. Versions and package IDs travel with the manifest; unsupported tools are shown before any execution."
            )
        )
        self.presets = QComboBox()
        for manifest in starter_presets():
            self.presets.addItem("Starter: " + manifest.name, manifest)
        layout.addWidget(self.presets)
        form = QFormLayout()
        self.collection_name = QLineEdit()
        self.collection_description = QLineEdit()
        form.addRow("Collection name", self.collection_name)
        form.addRow("Description", self.collection_description)
        layout.addLayout(form)
        self.packages = QTableWidget(0, 2)
        self.packages.setAccessibleName("Collection packages and versions")
        self.packages.setHorizontalHeaderLabels(
            ("Tool / package ID", "Exact package version (optional)")
        )
        self.packages.horizontalHeader().setStretchLastSection(True)
        self.packages.setColumnWidth(0, 430)
        self.packages.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        layout.addWidget(self.packages, 2)
        self.runtime_choices = QListWidget()
        self.runtime_choices.setAccessibleName("Collection runtime choices")
        self.runtime_choices.setMaximumHeight(100)
        layout.addWidget(
            text_label("Runtime choices (add exact versions on the Runtimes page):")
        )
        layout.addWidget(self.runtime_choices)
        actions = QHBoxLayout()
        self.save_collection = QPushButton("Save as session collection")
        self.import_button = QPushButton("Import manifest")
        self.export_button = QPushButton("Export manifest")
        self.preview_button = QPushButton("Preview for this platform")
        for button in (
            self.save_collection,
            self.import_button,
            self.export_button,
            self.preview_button,
        ):
            actions.addWidget(button)
        layout.addLayout(actions)
        self.manifest_details = QPlainTextEdit()
        self.manifest_details.setAccessibleName("Environment preview")
        self.manifest_details.setReadOnly(True)
        self.manifest_details.setPlaceholderText(
            "Preview lists selected sources, native commands, and unsupported items. Runtime/configuration choices are reviewed separately."
        )
        layout.addWidget(self.manifest_details, 1)
        self.package_button = QPushButton("Review package operations")
        self.package_button.setEnabled(False)
        layout.addWidget(self.package_button)
        self.manifest_status = text_label(
            "Collections are in memory until exported. Export chooses a new file and does not overwrite existing manifests."
        )
        layout.addWidget(self.manifest_status)
