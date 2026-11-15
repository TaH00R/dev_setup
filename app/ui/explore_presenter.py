"""ExplorePresenter coordinates widgets and existing services at the Qt boundary."""

from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, QUrl, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QListWidgetItem,
)

from models.package import PackageVersion
from models.planning import OperationAction
from models.specs import InstallSpec
from services.discovery import SearchSnapshot
from services.records import PackageRequest

if TYPE_CHECKING:
    from .main_window import MainWindow


class ExplorePresenter(QObject):
    def __init__(self, view: "MainWindow") -> None:
        super().__init__(view)
        self.view = view

    def _search(self, *, deferred: bool = False, force: bool = False) -> None:
        self.view.search_timer.stop()
        if self.view.state_loading or self.view.pending_close:
            return
        self.view.search_controller.request(
            self.view.search.text(),
            category=self.view.category.currentData(),
            mapped_only=self.view.compatible.isChecked(),
            enabled=self.view.live_enabled.isChecked(),
            deferred=deferred,
            force=force,
        )
        if deferred and self.view.search_controller.pending is not None:
            self.view.search_timer.start()

    @Slot(object)
    def _search_ready(self, snapshot: SearchSnapshot) -> None:
        if self.view.pending_close:
            return
        selected_id = self.view.package.id if self.view.package else None
        selected_live = self.view.live_package.key if self.view.live_package else None
        self.view.matches = [match.package for match in snapshot.curated]
        self.view.live_matches = list(snapshot.live)
        self.view.package_list.blockSignals(True)
        self.view.package_list.clear()
        for package in self.view.matches:
            mapped = any(
                s.support.supports(self.view.services.host)
                for s in package.install_specs
            )
            self.view.package_list.addItem(
                QListWidgetItem(
                    f"{package.name}\n{package.summary}\n{package.category.replace('-', ' ')} · Curated · {'Platform mapping available' if mapped else 'Unavailable on this platform'}"
                )
            )
        for live_package in self.view.live_matches:
            self.view.package_list.addItem(
                QListWidgetItem(
                    f"{live_package.name}\n{live_package.summary or live_package.native_id}\nLive · {live_package.backend} / {live_package.source_id} · {live_package.native_id}"
                )
            )
        index = next(
            (n for n, p in enumerate(self.view.matches) if p.id == selected_id), -1
        )
        same_curated = index >= 0
        if index < 0 and selected_live is not None:
            index = next(
                (
                    len(self.view.matches) + n
                    for n, p in enumerate(self.view.live_matches)
                    if p.key == selected_live
                ),
                -1,
            )
        if index < 0:
            index = 0 if self.view.package_list.count() else -1
        self.view.package_list.setCurrentRow(index)
        self.view.package_list.blockSignals(False)
        if not same_curated:
            self.view._select(index)
        states = []
        for status in snapshot.providers:
            message = f"{status.provider_id}: {status.state.value}"
            if status.cache_age_seconds is not None:
                message += f" ({int(status.cache_age_seconds)}s old)"
            if status.limited:
                message += " (result limit reached)"
            if status.error:
                message += f" · {status.error.message}"
            states.append(message)
        state = (
            "Search cancelled; partial results retained."
            if snapshot.cancelled
            else "Searching live sources…"
            if not snapshot.complete
            else "Search complete."
        )
        self.view.search_summary.setText(
            f"{len(self.view.matches)} curated · {len(self.view.live_matches)} live · {state}\n"
            + "\n".join(part for part in (snapshot.note, " | ".join(states)) if part)
        )

    @Slot(bool)
    def _search_busy(self, busy: bool) -> None:
        self.view.cancel_search.setVisible(busy)
        if not busy and self.view.pending_close and not self.view._operation_busy():
            QTimer.singleShot(0, self.view.close)

    def _live_details(self) -> None:
        package = self.view.live_package
        if package is None:
            return
        self.view.package_name.setText(package.name)
        self.view.package_details.setPlainText(
            "\n".join(
                [
                    package.summary or "No description supplied.",
                    "",
                    "Discovery: Live (not curated)",
                    f"Backend: {package.backend}",
                    f"Source: {package.source_id}",
                    f"Native ID: {package.native_id}",
                    f"Reported version: {package.version or 'Unknown'}",
                    f"Trust: {package.trust.value}",
                    f"Search platform: {package.platform.os.value} / {package.platform.architecture.value}",
                    "Platform compatibility and installation support have not been verified.",
                    "",
                    *package.caveats,
                ]
            )
        )
        self.view.homepage.setEnabled(False)
        self.view.source.setEnabled(False)
        self.view.action.setEnabled(False)
        self.view.version.clear()
        self.view.version.setEnabled(False)
        self.view.review_button.setEnabled(False)
        self.view.action_note.setText(
            "Live discovery only. Installation needs a reviewed catalog mapping and plan."
        )

    @Slot(int)
    def _select(self, index: int) -> None:
        live_index = index - len(self.view.matches)
        self.view.live_package = (
            self.view.live_matches[live_index]
            if 0 <= live_index < len(self.view.live_matches)
            else None
        )
        if self.view.live_package is not None:
            self.view.package = None
            self.view.source.blockSignals(True)
            self.view.source.clear()
            self.view.source.addItem(self.view.live_package.source_id, None)
            self.view.source.blockSignals(False)
            self.view._live_details()
            return
        self.view.package = (
            self.view.matches[index] if 0 <= index < len(self.view.matches) else None
        )
        self.view.source.setEnabled(self.view.package is not None)
        self.view.action.setEnabled(self.view.package is not None)
        self.view.source.clear()
        self.view.version.clear()
        package = self.view.package
        self.view.package_name.setText(
            package.name if package else "No package selected"
        )
        self.view.homepage.setEnabled(package is not None)
        if package:
            for spec in package.install_specs:
                if spec.support.supports(self.view.services.host):
                    self.view.source.addItem(
                        f"{spec.source.name} / {spec.backend} / {spec.native_id}", spec
                    )
        self.view._action_state()

    def _action_state(self) -> None:
        if self.view.live_package is not None:
            self.view._live_details()
            return
        spec = self.view.source.currentData()
        action = OperationAction(self.view.action.currentData())
        package = self.view.package
        lines = []
        if package:
            lines.extend(
                [
                    package.summary,
                    "",
                    f"Homepage: {package.homepage}",
                    f"Documentation: {package.documentation or 'Not listed'}",
                    f"License: {package.license or 'Not listed'}",
                    f"Vendor: {package.vendor or 'Not listed'}",
                    f"Category: {package.category}",
                    f"Dependencies: {', '.join(d.package_id for d in package.dependencies) or 'None declared'}",
                    f"Conflicts: {', '.join(package.conflicts) or 'None declared'}",
                ]
            )
        reason = "No reviewed mapping for this OS, architecture, or distribution."
        if isinstance(spec, InstallSpec) and isinstance(action, OperationAction):
            reason = (
                self.view.services.action_reason(spec, action)
                or "A fresh plan will show the exact operation before approval."
            )
            lines.extend(
                [
                    "",
                    f"Source: {spec.source.name} ({spec.source.kind.value}; {spec.source.trust.value})",
                    f"Source URL: {spec.source.url or 'Not listed'}",
                    f"Native ID: {spec.native_id}",
                    f"Workflow support: {self.view.services.workflow_support(spec, action).reason}",
                    f"Platform: {spec.support.os.value}; architectures: {', '.join(a.value for a in spec.support.architectures)}",
                    f"Privileges: {spec.privilege.value}",
                    f"Estimated size: {str(spec.estimated_size_bytes) + ' bytes' if spec.estimated_size_bytes is not None else 'Unknown'}",
                    f"Restart required: {'Yes' if spec.restart_required else 'Not declared'}",
                    *spec.notes,
                ]
            )
            can_select_version = self.view.services.version_selection(spec, action)
            self.view.version.setEnabled(
                can_select_version and not self.view._operation_busy()
            )
            if not can_select_version:
                self.view.version.clear()
            self.view.review_button.setEnabled(
                self.view.services.action_reason(spec, action) is None
                and not self.view._operation_busy()
            )
        else:
            self.view.review_button.setEnabled(False)
            self.view.version.setEnabled(False)
        self.view.package_details.setPlainText("\n".join(lines))
        self.view.action_note.setText(reason)

    def _homepage(self) -> None:
        if self.view.package:
            QDesktopServices.openUrl(QUrl(self.view.package.homepage))

    def _preview(self) -> None:
        spec = self.view.source.currentData()
        if self.view.package and isinstance(spec, InstallSpec):
            try:
                version = (
                    PackageVersion(self.view.version.text().strip())
                    if self.view.version.isEnabled()
                    and self.view.version.text().strip()
                    else None
                )
                request = PackageRequest(
                    self.view.package.id,
                    OperationAction(self.view.action.currentData()),
                    spec.backend,
                    spec.source.id,
                    version,
                )
            except ValueError:
                self.view._error("Enter a valid exact native version.")
                return
            self.view.controller.preview(request)
