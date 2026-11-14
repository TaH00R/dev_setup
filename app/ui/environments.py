"""Preset editing and explicit runtime/project reviews over GUI-independent services."""

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QListWidgetItem,
    QTableWidgetItem,
    QWidget,
)

from application.environments import EnvironmentWorkflow
from models.environment import (
    ConfigPlan,
    Manifest,
    ManifestPackage,
    RuntimeConsent,
    RuntimePlan,
    RuntimeRequest,
    RuntimeResult,
)
from models.package import PackageVersion
from runtimes.service import RuntimeService
from services.desktop import DesktopServices
from services.environments import (
    ManifestPreview,
    ProjectConfigService,
    starter_presets,
)
from system.commands import OutputSnapshot

from .change_review import ChangeReview as ChangeReview
from .components import scroll_page
from .environment_worker import EnvironmentController as EnvironmentController
from .pages.presets import PresetsPage
from .pages.runtimes import RuntimesPage


class EnvironmentPages(QObject):
    package_review = Signal(object)
    runtime_audit = Signal(object)
    busy_changed = Signal(bool)

    def __init__(
        self, services: DesktopServices, can_start: Callable[[], bool], parent: QWidget
    ) -> None:
        super().__init__(parent)
        self.services = services
        self.runtime_service = RuntimeService(services.host, services.inventory.runner)
        self.config_service = ProjectConfigService()
        self.workflow = EnvironmentWorkflow(services.planner, self.config_service)
        self.controller = EnvironmentController(can_start, self)
        self.controller.busy_changed.connect(self._busy)
        self.controller.completed.connect(self._done)
        self.controller.error.connect(self._error)
        self.controller.output.connect(self._output)
        self.collections: dict[str, Manifest] = {}
        self.base_manifest = starter_presets()[0]
        self.preview: ManifestPreview | None = None
        self.streams: dict[str, str] = {}
        self.presets_view = PresetsPage()
        self.presets = self.presets_view.presets
        self.collection_name = self.presets_view.collection_name
        self.collection_description = self.presets_view.collection_description
        self.packages = self.presets_view.packages
        self.runtime_choices = self.presets_view.runtime_choices
        self.save_collection = self.presets_view.save_collection
        self.import_button = self.presets_view.import_button
        self.export_button = self.presets_view.export_button
        self.preview_button = self.presets_view.preview_button
        self.manifest_details = self.presets_view.manifest_details
        self.package_button = self.presets_view.package_button
        self.manifest_status = self.presets_view.manifest_status
        self.presets.currentIndexChanged.connect(self._preset_changed)
        self.save_collection.clicked.connect(self._save_collection)
        self.import_button.clicked.connect(self._import_dialog)
        self.export_button.clicked.connect(self._export_dialog)
        self.preview_button.clicked.connect(self._preview_manifest)
        self.package_button.clicked.connect(self._review_packages)
        self.packages.itemChanged.connect(self._manifest_changed)
        self.runtime_choices.itemChanged.connect(self._manifest_changed)
        self.collection_name.textChanged.connect(self._manifest_changed)
        self.collection_description.textChanged.connect(self._manifest_changed)
        self.presets_page = self.presets_view
        self.runtimes_view = RuntimesPage(self.runtime_service.managers())
        self.manager = self.runtimes_view.manager
        self.runtime = self.runtimes_view.runtime
        self.runtime_version = self.runtimes_view.runtime_version
        self.runtime_preview = self.runtimes_view.runtime_preview
        self.add_runtime = self.runtimes_view.add_runtime
        self.project = self.runtimes_view.project
        self.project_browse = self.runtimes_view.project_browse
        self.config_kind = self.runtimes_view.config_kind
        self.config_version = self.runtimes_view.config_version
        self.config_preview = self.runtimes_view.config_preview
        self.env_name = self.runtimes_view.env_name
        self.env_path = self.runtimes_view.env_path
        self.env_preview = self.runtimes_view.env_preview
        self.runtime_status = self.runtimes_view.runtime_status
        self.runtime_output = self.runtimes_view.runtime_output
        self.manager.currentIndexChanged.connect(self._manager_changed)
        self.runtime_preview.clicked.connect(self._preview_runtime)
        self.add_runtime.clicked.connect(self._add_runtime)
        self.project_browse.clicked.connect(self._project_dialog)
        self.config_preview.clicked.connect(self._preview_config)
        self.env_preview.clicked.connect(self._preview_environment)
        self.runtimes_page = scroll_page(self.runtimes_view)
        self._manager_changed()
        self._load_manifest(self.base_manifest)

    def _preset_changed(self) -> None:
        manifest = self.presets.currentData()
        if isinstance(manifest, Manifest):
            self._load_manifest(manifest)

    def _load_manifest(self, manifest: Manifest) -> None:
        self.base_manifest = manifest
        self.preview = None
        self.collection_name.setText(manifest.name)
        self.collection_description.setText(manifest.description)
        selected = {p.package_id: p for p in manifest.packages}
        package_ids = [p.id for p in self.services.catalog.packages]
        package_ids.extend(p for p in selected if p not in package_ids)
        self.packages.setRowCount(len(package_ids))
        for row, package_id in enumerate(package_ids):
            package = self.services.catalog.get(package_id)
            item = QTableWidgetItem(
                f"{package.name if package else 'Unknown tool'} ({package_id})"
            )
            item.setData(Qt.ItemDataRole.UserRole, package_id)
            item.setFlags(
                Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
            )
            item.setCheckState(
                Qt.CheckState.Checked
                if package_id in selected
                else Qt.CheckState.Unchecked
            )
            self.packages.setItem(row, 0, item)
            version = selected[package_id].version if package_id in selected else None
            self.packages.setItem(
                row, 1, QTableWidgetItem(version.value if version else "")
            )
        self.runtime_choices.clear()
        for request in manifest.runtimes:
            self._runtime_item(request)
        self.manifest_details.setPlainText(
            manifest.description
            + "\n\nProject configuration choices and relative environment paths are preserved on import/export and reviewed separately on Runtimes."
        )
        self.package_button.setEnabled(False)

    def _manifest(self) -> Manifest:
        packages = []
        for row in range(self.packages.rowCount()):
            item = self.packages.item(row, 0)
            version_item = self.packages.item(row, 1)
            if item is None or version_item is None:
                raise ValueError("Incomplete collection row.")
            if item.checkState() == Qt.CheckState.Checked:
                version = version_item.text().strip()
                packages.append(
                    ManifestPackage(
                        item.data(Qt.ItemDataRole.UserRole),
                        PackageVersion(version) if version else None,
                    )
                )
        runtimes = tuple(
            self.runtime_choices.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.runtime_choices.count())
            if self.runtime_choices.item(i).checkState() == Qt.CheckState.Checked
        )
        return replace(
            self.base_manifest,
            name=self.collection_name.text().strip(),
            description=self.collection_description.text().strip(),
            packages=tuple(packages),
            runtimes=runtimes,
        )

    def _save_collection(self) -> None:
        try:
            manifest = self._manifest()
        except ValueError:
            self._error(
                "Choose a valid collection name, package versions and unique runtime choices."
            )
            return
        self.collections[manifest.name] = manifest
        index = self.presets.findText("User: " + manifest.name)
        if index >= 0:
            self.presets.setItemData(index, manifest)
            self.presets.setCurrentIndex(index)
            self._load_manifest(manifest)
        else:
            self.presets.addItem("User: " + manifest.name, manifest)
            self.presets.setCurrentIndex(self.presets.count() - 1)
        self.manifest_status.setText(
            "Collection saved for this session. Export it to keep a portable copy."
        )

    def _manifest_changed(self) -> None:
        self.preview = None
        self.package_button.setEnabled(False)

    def _import_dialog(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self.presets_page,
            "Import environment manifest",
            "",
            "JSON manifests (*.json)",
        )
        if filename:
            self.import_manifest(Path(filename))

    def import_manifest(self, path: Path) -> bool:
        def task(cancel: Event, event: object, output: object) -> Manifest:
            return self.workflow.import_manifest(path, cancel)

        return self.controller.start("import", task)

    def _export_dialog(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(
            self.presets_page,
            "Export to a new manifest file",
            "",
            "JSON manifests (*.json)",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if filename:
            self.export_manifest(Path(filename))

    def export_manifest(self, path: Path) -> bool:
        try:
            manifest = self._manifest()
        except ValueError:
            self._error("Choose a valid collection name and exact package versions.")
            return False

        def task(cancel: Event, event: object, output: object) -> Path:
            return self.workflow.export_manifest(manifest, path, cancel)

        return self.controller.start("export", task)

    def _preview_manifest(self) -> None:
        try:
            manifest = self._manifest()
        except ValueError:
            self._error("Choose a valid collection name and exact package versions.")
            return
        self.controller.start(
            "manifest-preview",
            lambda cancel, event, output: self.workflow.preview(manifest, cancel),
        )

    def _review_packages(self) -> None:
        if (
            self.preview
            and self.preview.ready
            and not self.controller.busy
            and self.controller.can_start()
        ):
            self.package_review.emit(self.preview.reviewed)

    def _manager_changed(self) -> None:
        manager = self.manager.currentData()
        self.runtime.clear()
        if manager is not None:
            self.runtime.addItems(manager.runtimes)

    def _runtime_request(self) -> RuntimeRequest:
        return RuntimeRequest(
            self.manager.currentText(),
            self.runtime.currentText(),
            self.runtime_version.text().strip(),
        )

    def _runtime_item(self, request: RuntimeRequest) -> None:
        item = QListWidgetItem(
            f"{request.manager}: {request.runtime}@{request.version}"
        )
        item.setData(Qt.ItemDataRole.UserRole, request)
        item.setFlags(
            Qt.ItemFlag.ItemIsUserCheckable
            | Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
        )
        item.setCheckState(Qt.CheckState.Checked)
        self.runtime_choices.addItem(item)

    def _add_runtime(self) -> None:
        try:
            request = self._runtime_request()
        except ValueError:
            self._error("Select a supported runtime and an exact numeric version.")
            return
        for index in range(self.runtime_choices.count()):
            current = self.runtime_choices.item(index).data(Qt.ItemDataRole.UserRole)
            if (current.manager, current.runtime) == (request.manager, request.runtime):
                self.runtime_choices.takeItem(index)
                break
        self._runtime_item(request)
        self.runtime_status.setText(
            "Runtime choice added to the current collection. Save or export it on Presets."
        )

    def _preview_runtime(self) -> None:
        try:
            request = self._runtime_request()
        except ValueError:
            self._error(
                "Select a supported runtime and an exact numeric version, for example 3.14.0."
            )
            return
        self.controller.start(
            "runtime-preview",
            lambda cancel, event, output: self.runtime_service.preview(
                request, cancel=cancel
            ),
        )

    def _project_dialog(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self.runtimes_page, "Choose project directory"
        )
        if directory:
            self.project.setText(directory)

    def _preview_config(self) -> None:
        if not self.project.text().strip():
            self._error(
                "Choose an existing project directory before previewing changes."
            )
            return
        project, kind, version = (
            Path(self.project.text()),
            self.config_kind.currentData(),
            self.config_version.text().strip(),
        )
        self.controller.start(
            "config-preview",
            lambda cancel, event, output: self.config_service.preview(
                project, kind, version
            ),
        )

    def _preview_environment(self) -> None:
        if not self.project.text().strip():
            self._error(
                "Choose an existing project directory before previewing changes."
            )
            return
        project, name, relative_path = (
            Path(self.project.text()),
            self.env_name.currentText(),
            self.env_path.text().strip(),
        )
        self.controller.start(
            "config-preview",
            lambda cancel, event, output: self.config_service.preview_environment(
                project, name, relative_path
            ),
        )

    @Slot(bool)
    def _busy(self, busy: bool) -> None:
        self._controls_busy(busy)
        if busy:
            self.runtime_status.setText(
                "Working in the background. The current task can be cancelled from the footer."
            )
        self.busy_changed.emit(busy)

    def _controls_busy(self, busy: bool) -> None:
        for button in (
            self.save_collection,
            self.import_button,
            self.export_button,
            self.preview_button,
            self.runtime_preview,
            self.add_runtime,
            self.config_preview,
            self.env_preview,
        ):
            button.setEnabled(not busy)
        self.package_button.setEnabled(
            not busy and self.preview is not None and self.preview.ready
        )

    def set_external_busy(self, busy: bool) -> None:
        self._controls_busy(busy or self.controller.busy)

    @Slot(str)
    def _error(self, message: str) -> None:
        self.runtime_status.setText(message)
        self.manifest_status.setText(message)

    @Slot(str, object)
    def _output(self, operation_id: str, snapshot: OutputSnapshot) -> None:
        self.streams[snapshot.stream] = snapshot.text[-65536:] + (
            "\n[Output truncated]" if snapshot.truncated else ""
        )
        self.runtime_output.setPlainText(
            "\n\n".join(
                f"{key.upper()}\n{value}" for key, value in self.streams.items()
            )
        )

    @Slot(str, object)
    def _done(self, kind: str, value: object) -> None:
        if kind == "import" and isinstance(value, Manifest):
            self._load_manifest(value)
            self.manifest_status.setText(
                "Manifest imported. Preview it for this platform before any operation."
            )
        elif kind == "export":
            self.manifest_status.setText("Manifest exported to the selected new file.")
        elif kind == "manifest-preview" and isinstance(value, ManifestPreview):
            try:
                current = self._manifest()
            except ValueError:
                current = None
            if current != value.manifest:
                self.manifest_status.setText(
                    "Collection changed while the preview was prepared. Preview it again."
                )
                return
            self.preview = value
            lines = [
                f"Manifest: {value.manifest.name}",
                f"Target: {self.services.host.os.value} / {self.services.host.architecture.value}",
            ]
            lines.extend(f"Unsupported: {message}" for message in value.unsupported)
            lines.extend(
                f"Issue: {issue.message}" for issue in value.reviewed.plan.issues
            )
            for item in value.reviewed.plan.items:
                lines.extend(
                    [
                        "",
                        f"{item.action.value}: {item.package_id}",
                        f"{item.spec.backend} / {item.spec.source.id} / {item.spec.native_id}",
                    ]
                )
                lines.extend(
                    "Arguments: " + json.dumps(command.display_args)
                    for command in item.commands
                )
                if item.skip_reason:
                    lines.append("Skip: " + item.skip_reason)
            for runtime in value.manifest.runtimes:
                lines.append(
                    f"Separate runtime review: {runtime.manager} {runtime.runtime}@{runtime.version}"
                )
            lines.append(
                "Project/configuration choices are preserved; review each project file separately on Runtimes."
            )
            self.manifest_details.setPlainText("\n".join(lines))
            self.package_button.setEnabled(value.ready and self.controller.can_start())
            self.manifest_status.setText(
                "Preview ready. Unsupported items must be resolved explicitly; no substitutions were made."
            )
        elif kind == "runtime-preview" and isinstance(value, RuntimePlan):
            self._review_runtime(value)
        elif kind == "config-preview" and isinstance(value, ConfigPlan):
            self._review_config(value)
        elif kind == "runtime-execute" and isinstance(value, RuntimeResult):
            if value.audit is not None:
                self.runtime_audit.emit(value.audit)
            self.runtime_status.setText(
                f"{value.state.value}: {value.message}"
                + (
                    f" {value.error.code.value}: {value.error.message}"
                    if value.error
                    else ""
                )
                + (
                    " " + " ".join(value.persistence_errors)
                    if value.persistence_errors
                    else ""
                )
            )
            if value.command_result:
                result = value.command_result
                self._output(
                    "runtime",
                    OutputSnapshot("stdout", result.stdout, result.output_truncated),
                )
                self._output(
                    "runtime",
                    OutputSnapshot("stderr", result.stderr, result.output_truncated),
                )
        elif kind == "config-apply":
            self.runtime_status.setText(
                "Project configuration applied after review. Global environment and shell startup files were not changed."
            )

    def _review_runtime(self, plan: RuntimePlan) -> None:
        lines = [
            f"Manager: {plan.request.manager}",
            f"Runtime: {plan.request.runtime}",
            f"Version: {plan.request.version}",
            f"Installed versions: {', '.join(plan.installed) or 'None recorded'}",
            *plan.notes,
        ]
        if plan.command:
            lines.extend(
                [
                    f"Source: {plan.command.source_id}",
                    f"Privileges: {plan.command.privilege.value}",
                    "Arguments: " + json.dumps(plan.command.preview().display_args),
                ]
            )
        if plan.skip_reason:
            lines.append("Skip: " + plan.skip_reason)
        dialog = ChangeReview(
            "Review runtime installation",
            "\n".join(lines),
            required=plan.command is not None,
            parent=self.runtimes_page,
        )
        try:
            if (
                dialog.exec() == QDialog.DialogCode.Accepted
                and dialog.run_button.isEnabled()
            ):
                consent = RuntimeConsent(plan, True)
                self.controller.start(
                    "runtime-execute",
                    lambda cancel, event, output: self.runtime_service.execute(
                        plan,
                        consent=consent,
                        cancel=cancel,
                        observe=lambda snapshot: output(plan.id, snapshot),
                    ),
                    from_review=True,
                )
            else:
                self.runtime_status.setText(
                    "Runtime review closed. No runtime installation was started."
                )
        finally:
            self.controller.release_review()
            dialog.deleteLater()

    def _review_config(self, plan: ConfigPlan) -> None:
        details = "\n".join(
            [
                f"Project: {plan.project}",
                f"File: {plan.path}",
                *plan.notes,
                "",
                "CURRENT CONTENT",
                plan.previous_content or "(new file)",
                "",
                "REVIEWED CONTENT",
                plan.content,
            ]
        )
        dialog = ChangeReview(
            "Review project file changes", details, parent=self.runtimes_page
        )
        try:
            if (
                dialog.exec() == QDialog.DialogCode.Accepted
                and dialog.approve.isChecked()
            ):

                def apply(cancel: Event, event: object, output: object) -> Path:
                    return self.workflow.apply_config(
                        plan, confirmed=True, cancel=cancel
                    )

                self.controller.start("config-apply", apply, from_review=True)
            else:
                self.runtime_status.setText(
                    "Project review closed. No file was changed."
                )
        finally:
            self.controller.release_review()
            dialog.deleteLater()
