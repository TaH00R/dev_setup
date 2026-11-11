"""Supplement native package identity with safe executable observations."""

import shutil
from collections.abc import Iterable
from pathlib import Path
from threading import Event
from typing import Protocol

from models.catalog import Package
from models.errors import ErrorCode, OperationError
from models.inventory import Detection, Inventory, InventoryReport, NativeReview
from models.planning import OperationAction
from models.specs import InstallSpec
from package_managers.base import AdapterError, PackageManager
from system.commands import Command
from system.runner import CommandRunner


class Inspector(Protocol):
    adapter: PackageManager

    def inventory(self, cancel: Event | None = None) -> Inventory: ...

    def review(
        self, spec: InstallSpec, action: OperationAction, cancel: Event | None = None
    ) -> NativeReview: ...


class InventoryService:
    def __init__(self, inspectors: Iterable[Inspector], runner: CommandRunner) -> None:
        self.inspectors = tuple(inspectors)
        if (
            len({i.adapter.id for i in self.inspectors}) != len(self.inspectors)
            or len({i.adapter.host for i in self.inspectors}) > 1
        ):
            raise ValueError("inventory inspectors must be unique and share a host")
        self.runner = runner

    def collect(
        self, packages: tuple[Package, ...] = (), *, cancel: Event | None = None
    ) -> InventoryReport:
        snapshots = []
        errors = []
        detections: list[Detection] = []
        for inspector in self.inspectors:
            try:
                snapshots.append(inspector.inventory(cancel))
            except AdapterError as error:
                errors.append((inspector.adapter.id, error.error))
            for package in packages:
                for rule in package.detection:
                    if rule.support and not rule.support.supports(
                        inspector.adapter.host
                    ):
                        continue
                    key = (package.id, rule.executable)
                    if any((d.package_id, d.executable) == key for d in detections):
                        continue
                    path = shutil.which(rule.executable)
                    if path is None:
                        detections.append(Detection(*key, False))
                    elif not Path(path).is_absolute():
                        detections.append(
                            Detection(
                                *key,
                                True,
                                error=OperationError(
                                    ErrorCode.VERIFICATION_FAILED,
                                    "Executable resolution did not return an absolute path.",
                                ),
                            )
                        )
                    else:
                        command = Command(
                            (path, *rule.version_args),
                            "Read executable version; not proof of package identity or service health",
                            inspector.adapter.host.os,
                            "detection",
                            timeout_seconds=rule.timeout_seconds,
                        )
                        result = self.runner.run(command, cancel=cancel)
                        detection_error = result.error
                        if detection_error is None and (
                            not result.succeeded or result.output_truncated
                        ):
                            detection_error = OperationError(
                                ErrorCode.VERIFICATION_FAILED,
                                "Executable version observation failed or was truncated.",
                            )
                        detections.append(
                            Detection(*key, True, result.stdout, detection_error)
                        )
        return InventoryReport(tuple(snapshots), tuple(detections), tuple(errors))
