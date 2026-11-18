"""Supplement native package identity with safe executable observations."""

import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Protocol

from models.catalog import Package
from models.errors import ErrorCode, OperationError
from models.inventory import Detection, Inventory, InventoryReport, NativeReview
from models.planning import OperationAction
from models.platform import Platform
from models.specs import InstallSpec
from package_managers.base import AdapterError, PackageManager
from system.commands import Command
from system.coordination import NativeCoordinator
from system.platform import detect_platform
from system.runner import CommandRunner

from .coordination import access


class Inspector(Protocol):
    adapter: PackageManager

    def inventory(self, cancel: Event | None = None) -> Inventory: ...

    def review(
        self, spec: InstallSpec, action: OperationAction, cancel: Event | None = None
    ) -> NativeReview: ...


@dataclass(frozen=True)
class InventorySource:
    """Read-only inventory does not grant planning or mutation capabilities."""

    backend: str
    read: Callable[[Event | None], Inventory]


class InventoryService:
    def __init__(
        self,
        inspectors: Iterable[Inspector],
        runner: CommandRunner,
        *,
        host: Platform | None = None,
        sources: Iterable[InventorySource] = (),
    ) -> None:
        self.inspectors = tuple(inspectors)
        if (
            len({i.adapter.id for i in self.inspectors}) != len(self.inspectors)
            or len({i.adapter.host for i in self.inspectors}) > 1
        ):
            raise ValueError("inventory inspectors must be unique and share a host")
        self.runner = runner
        self.host = host or (
            self.inspectors[0].adapter.host if self.inspectors else detect_platform()
        )
        supplied = tuple(sources)
        self.sources = (
            tuple(
                InventorySource(i.adapter.id, i.inventory)
                for i in self.inspectors
                if i.adapter.id not in {s.backend for s in supplied}
            )
            + supplied
        )
        if len({s.backend for s in self.sources}) != len(self.sources):
            raise ValueError("inventory sources must be unique")
        self.coordinator = NativeCoordinator()

    def collect(
        self, packages: tuple[Package, ...] = (), *, cancel: Event | None = None
    ) -> InventoryReport:
        with access(self.coordinator, cancel):
            return self._collect(packages, cancel=cancel)

    def _collect(
        self, packages: tuple[Package, ...] = (), *, cancel: Event | None = None
    ) -> InventoryReport:
        snapshots = []
        errors = []
        detections: list[Detection] = []
        for source in self.sources:
            if cancel and cancel.is_set():
                break
            try:
                snapshot = source.read(cancel)
                if snapshot.backend != source.backend:
                    raise AdapterError(
                        ErrorCode.VERIFICATION_FAILED,
                        "Inventory backend identity does not match its reader.",
                    )
                snapshots.append(snapshot)
            except AdapterError as error:
                errors.append((source.backend, error.error))
        for package in packages:
            for rule in package.detection:
                if cancel and cancel.is_set():
                    return InventoryReport(
                        tuple(snapshots), tuple(detections), tuple(errors)
                    )
                if rule.support and not rule.support.supports(self.host):
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
                        self.host.os,
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
                        Detection(
                            *key, True, result.stdout or result.stderr, detection_error
                        )
                    )
        return InventoryReport(tuple(snapshots), tuple(detections), tuple(errors))
