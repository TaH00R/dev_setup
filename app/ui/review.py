"""Plain-text plan review with independent operation and elevation decisions."""

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from models.specs import PrivilegeRequirement
from services.records import PlanConsent, ReviewedPlan


class ReviewDialog(QDialog):
    def __init__(self, reviewed: ReviewedPlan, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.reviewed = reviewed
        self.setWindowTitle("Review package operations")
        self.resize(760, 620)
        layout = QVBoxLayout(self)
        title = QLabel("Review before making changes")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        lines = [
            f"Plan: {reviewed.plan.id}",
            f"Platform: {reviewed.plan.platform.os.value} / {reviewed.plan.platform.architecture.value}",
        ]
        for issue in reviewed.plan.issues:
            lines.append(f"Cannot proceed ({issue.code.value}): {issue.message}")
        for item, prepared in zip(reviewed.plan.items, reviewed.prepared):
            spec = item.spec
            version = (
                prepared.review.version
                if prepared.review
                else spec.version.value
                if spec.version
                else "Native default / installed version"
            )
            privilege = (
                prepared.command.privilege
                if prepared.command
                else PrivilegeRequirement.NONE
            )
            lines.extend(
                [
                    "",
                    f"{item.action.value.upper()} {item.package_id}",
                    f"Source: {spec.source.name} ({spec.source.id}) — {spec.source.url or 'URL unavailable'}",
                    f"Backend: {spec.backend}; native ID: {spec.native_id}",
                    f"Source trust: {spec.source.trust.value.replace('_', ' ')}; {spec.source.kind.value} metadata",
                    f"Version: {version}",
                    f"Planned privilege requirement: {privilege.value}",
                    f"Installed native versions: {', '.join(prepared.installed.versions) if prepared.installed else 'Not present in the selected native inventory'}",
                    f"Installed source: {prepared.installed.source_id if prepared.installed and prepared.installed.source_id else 'Unknown / not recorded'}",
                    f"Dependencies: {', '.join(item.dependencies) or 'None declared'}",
                    f"Estimated size: {str(spec.estimated_size_bytes) + ' bytes' if spec.estimated_size_bytes is not None else 'Unknown'}",
                    f"Restart required: {'Yes' if spec.restart_required else 'Not declared'}",
                ]
            )
            if item.skip_reason:
                lines.append(f"Skip: {item.skip_reason}")
            for command in item.commands:
                lines.extend(
                    [
                        command.description,
                        "Arguments: "
                        + json.dumps(command.display_args, ensure_ascii=False),
                    ]
                )
            lines.extend(item.changes)
        self.details.setPlainText("\n".join(lines))
        layout.addWidget(self.details)
        self.approve = QCheckBox("I approve the package operations shown above.")
        self.approve.setVisible(reviewed.plan.requires_consent)
        layout.addWidget(self.approve)
        self.needs_elevation = any(
            p.command and p.command.privilege != PrivilegeRequirement.NONE
            for p in reviewed.prepared
        )
        self.elevation = QCheckBox(
            "Allow the native administrator prompt for this plan. No password is stored."
        )
        self.elevation.setVisible(self.needs_elevation)
        layout.addWidget(self.elevation)
        note = QLabel(
            "Cancellation is best effort. Partial changes can remain; rollback is not available."
        )
        note.setTextFormat(Qt.TextFormat.PlainText)
        note.setWordWrap(True)
        layout.addWidget(note)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self.run_button = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.run_button.setText(
            "Approve and run"
            if reviewed.plan.requires_consent
            else "Run verification / finish review"
        )
        self.run_button.setDefault(False)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setDefault(True)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.approve.toggled.connect(self._ready)
        self.elevation.toggled.connect(self._ready)
        layout.addWidget(self.buttons)
        self._ready()

    def _ready(self) -> None:
        self.run_button.setEnabled(
            self.reviewed.plan.ready
            and (not self.reviewed.plan.requires_consent or self.approve.isChecked())
            and (not self.needs_elevation or self.elevation.isChecked())
        )

    def consent(self) -> PlanConsent | None:
        if (
            self.result() != QDialog.DialogCode.Accepted
            or not self.run_button.isEnabled()
        ):
            return None
        if not self.reviewed.plan.requires_consent:
            return None
        return PlanConsent(
            self.reviewed,
            confirmed=self.approve.isChecked(),
            elevation=self.elevation.isChecked(),
        )
