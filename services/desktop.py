"""Desktop-facing application services. This module does not import Qt."""

from dataclasses import dataclass
from threading import Event

from models.inventory import InventoryReport
from models.planning import OperationAction
from models.platform import Platform
from models.specs import InstallSpec
from package_managers.base import Availability, BackendAction, PackageManager
from system.runner import CommandRunner

from .catalog.service import CatalogService
from .inventory import Inspector, InventoryService
from .operations import OperationService
from .planning import Planner
from .records import PackageRequest


@dataclass(frozen=True)
class InstalledRow:
    native_id: str
    versions: tuple[str, ...]
    backend: str
    source_id: str | None
    package_id: str | None
    mapping: InstallSpec | None


@dataclass(frozen=True)
class DesktopSnapshot:
    backends: tuple[Availability, ...]
    inventory: InventoryReport
    installed: tuple[InstalledRow, ...]


@dataclass(frozen=True)
class UpdateCandidate:
    package_id: str
    spec: InstallSpec
    installed: tuple[str, ...]
    candidate: str


@dataclass(frozen=True)
class UpdateReport:
    candidates: tuple[UpdateCandidate, ...]
    errors: tuple[str, ...]
    cancelled: bool = False


class DesktopServices:
    def __init__(
        self,
        catalog: CatalogService,
        host: Platform,
        adapters: tuple[PackageManager, ...],
        inspectors: tuple[Inspector, ...],
        runner: CommandRunner,
    ) -> None:
        self.catalog = catalog
        self.host = host
        self.adapters = adapters
        self.inspectors = inspectors
        self.inventory = InventoryService(inspectors, runner)
        self.planner = Planner(catalog, host, inspectors)
        self.operations = OperationService(inspectors, runner)

    def action_reason(self, spec: InstallSpec, action: OperationAction) -> str | None:
        inspector = next(
            (i for i in self.inspectors if i.adapter.id == spec.backend), None
        )
        if inspector is None:
            return f"Managing packages through {spec.backend} is not available in this version."
        availability = inspector.adapter.availability()
        if not availability.available:
            return availability.reason or "The selected package manager is unavailable."
        if (
            action != OperationAction.VERIFY
            and BackendAction(action.value)
            not in inspector.adapter.capabilities.actions
        ):
            return "The selected package manager does not support this action."
        return None

    def version_selection(self, spec: InstallSpec, action: OperationAction) -> bool:
        if action == OperationAction.VERIFY:
            return True
        adapter = next(
            (i.adapter for i in self.inspectors if i.adapter.id == spec.backend), None
        )
        return (
            adapter is not None
            and BackendAction(action.value) in adapter.capabilities.version_selection
        )

    def refresh(self, cancel: Event) -> DesktopSnapshot:
        backends = tuple(adapter.availability() for adapter in self.adapters)
        report = self.inventory.collect(self.catalog.packages, cancel=cancel)
        rows = []
        for snapshot in report.snapshots:
            for installed in snapshot.packages:
                matches = [
                    (package.id, spec)
                    for package in self.catalog.packages
                    for spec in package.install_specs
                    if spec.backend == snapshot.backend
                    and spec.support.supports(self.host)
                    and snapshot.get(spec.native_id) == installed
                    and (
                        installed.source_id is None
                        or installed.source_id == spec.source.id
                    )
                ]
                package_id, mapping = matches[0] if len(matches) == 1 else (None, None)
                rows.append(
                    InstalledRow(
                        installed.native_id,
                        installed.versions,
                        snapshot.backend,
                        installed.source_id,
                        package_id,
                        mapping,
                    )
                )
        return DesktopSnapshot(backends, report, tuple(rows))

    def updates(self, snapshot: DesktopSnapshot, cancel: Event) -> UpdateReport:
        candidates: list[UpdateCandidate] = []
        errors: list[str] = [
            f"{backend}: {error.message}"
            for backend, error in snapshot.inventory.errors
        ]
        if not snapshot.inventory.snapshots:
            errors.append(
                "Native inventory is unavailable; installed tools could not be checked for updates."
            )
        for row in snapshot.installed:
            if cancel.is_set():
                return UpdateReport(tuple(candidates), tuple(errors), True)
            if row.package_id is None or row.mapping is None:
                continue
            reason = self.action_reason(row.mapping, OperationAction.UPGRADE)
            if reason:
                errors.append(f"{row.package_id}: {reason}")
                continue
            reviewed = self.planner.preview(
                (
                    PackageRequest(
                        row.package_id,
                        OperationAction.UPGRADE,
                        row.backend,
                        row.mapping.source.id,
                    ),
                ),
                cancel=cancel,
            )
            if reviewed.plan.issues:
                errors.extend(
                    f"{row.package_id}: {issue.message}"
                    for issue in reviewed.plan.issues
                )
                continue
            for item, prepared in zip(reviewed.plan.items, reviewed.prepared):
                if (
                    item.package_id == row.package_id
                    and not item.skip_reason
                    and prepared.review
                    and prepared.review.version
                ):
                    candidates.append(
                        UpdateCandidate(
                            row.package_id,
                            row.mapping,
                            row.versions,
                            prepared.review.version,
                        )
                    )
        return UpdateReport(tuple(candidates), tuple(errors), cancel.is_set())
