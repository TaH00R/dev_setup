"""SettingsPage controls, independent of the main window."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from services.desktop import (
    DesktopServices,
)

from ..components import label
from ..support import SupportControls


class SettingsPage(QWidget):
    def __init__(
        self,
        services: DesktopServices,
        *,
        persistent: bool = False,
        loading: bool = False,
    ) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        self.setObjectName("page")
        layout.setContentsMargins(0, 0, 0, 0)
        appearance = QGroupBox("Appearance and search")
        form = QFormLayout(appearance)
        self.theme = QComboBox()
        self.theme.addItems(("Light", "Dark"))
        self.theme.setCurrentIndex(1)
        form.addRow("Theme", self.theme)
        self.debounce = QSpinBox()
        self.debounce.setRange(150, 1000)
        self.debounce.setValue(250)
        self.debounce.setSuffix(" ms")
        form.addRow("Search delay", self.debounce)
        self.live_search_setting = QCheckBox(
            "Search detected managers (may access the network)"
        )
        self.live_search_setting.setChecked(True)
        form.addRow("Live discovery", self.live_search_setting)
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
                            for p in services.catalog.packages
                            for spec in p.install_specs
                            if spec.support.supports(services.host)
                        }
                    )
                )
                or "No reviewed source mappings for this platform."
            )
        )
        layout.addWidget(sources)
        privacy = QGroupBox("Privacy and history")
        privacy_layout = QVBoxLayout(privacy)
        privacy_layout.addWidget(
            label(
                "Process output stays in this session. No telemetry is collected."
                if persistent
                else "Settings and history are kept in memory. Native administrator prompts handle passwords. No telemetry is collected."
            )
        )
        self.persistence_note = label(
            "Loading saved preferences…" if loading else "Session-only mode.",
            "muted",
        )
        privacy_layout.addWidget(self.persistence_note)
        self.clear_button = QPushButton(
            "Clear saved history and session logs"
            if persistent
            else "Clear session history and logs"
        )
        privacy_layout.addWidget(
            self.clear_button, alignment=Qt.AlignmentFlag.AlignLeft
        )
        layout.addWidget(privacy)
        self.support = SupportControls(services.host, self)
        layout.addWidget(self.support.group)
        layout.addStretch()
