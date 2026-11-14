"""RuntimesPage controls, independent of the main window."""

from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from runtimes.adapters import RuntimeManager

from ..components import text_label


class RuntimesPage(QWidget):
    def __init__(self, managers: tuple[RuntimeManager, ...]) -> None:
        super().__init__()
        page = self
        page.setObjectName("page")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        runtime_group = QGroupBox("Runtime versions")
        form = QFormLayout(runtime_group)
        self.manager = QComboBox()
        for manager in managers:
            self.manager.addItem(manager.id, manager)
        self.runtime = QComboBox()
        self.runtime_version = QLineEdit()
        self.runtime_version.setPlaceholderText(
            "Exact numeric version, for example 3.14.0"
        )
        form.addRow("Existing manager", self.manager)
        form.addRow("Runtime", self.runtime)
        form.addRow("Version", self.runtime_version)
        self.runtime_preview = QPushButton("Review runtime installation")
        form.addRow(self.runtime_preview)
        self.add_runtime = QPushButton("Add or update runtime choice in collection")
        form.addRow(self.add_runtime)
        form.addRow(
            text_label(
                "Managers must already be installed. Moving channels and automatic manager bootstrap are not available. SDKMAN!, Conda/Mamba and mise Java remain staged where reviewed support is unavailable."
            )
        )
        layout.addWidget(runtime_group)
        project_group = QGroupBox("Project-local configuration")
        project_form = QFormLayout(project_group)
        project_row = QHBoxLayout()
        self.project = QLineEdit()
        self.project.setAccessibleName("Project directory")
        self.project.setPlaceholderText("Choose an existing project directory")
        project_row.addWidget(self.project, 1)
        self.project_browse = QPushButton("Browse")
        project_row.addWidget(self.project_browse)
        project_form.addRow("Project", project_row)
        self.config_kind = QComboBox()
        for title, kind in (
            ("Python version (.python-version)", "python"),
            ("Rust toolchain", "rust"),
            ("mise Python", "mise-python"),
            ("mise Node", "mise-node"),
        ):
            self.config_kind.addItem(title, kind)
        self.config_version = QLineEdit()
        self.config_version.setPlaceholderText("Exact numeric version")
        project_form.addRow("Configuration", self.config_kind)
        project_form.addRow("Version", self.config_version)
        self.config_preview = QPushButton("Review project file")
        project_form.addRow(self.config_preview)
        self.env_name = QComboBox()
        self.env_name.addItems(("JAVA_HOME", "PYTHONPATH", "GOPATH", "CARGO_HOME"))
        self.env_path = QLineEdit()
        self.env_path.setPlaceholderText("Relative project path, for example .venv")
        project_form.addRow("Project path variable", self.env_name)
        project_form.addRow("Relative path", self.env_path)
        self.env_preview = QPushButton("Review project environment file")
        project_form.addRow(self.env_preview)
        project_form.addRow(
            text_label(
                "These actions preview owned project files. They do not edit global PATH, shell startup files, services, or system settings."
            )
        )
        layout.addWidget(project_group)
        self.runtime_status = text_label(
            "Choose an installed manager and an exact version to prepare a review."
        )
        layout.addWidget(self.runtime_status)
        self.runtime_output = QPlainTextEdit()
        self.runtime_output.setAccessibleName("Runtime operation output")
        self.runtime_output.setReadOnly(True)
        self.runtime_output.setMaximumHeight(140)
        layout.addWidget(self.runtime_output)
