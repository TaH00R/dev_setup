"""Portable environment file workflows shared by desktop and future CLI callers."""

from pathlib import Path
from threading import Event

from models.environment import ConfigPlan, Manifest
from models.errors import ErrorCode
from package_managers.base import AdapterError
from services.environments import (
    ManifestPreview,
    ProjectConfigService,
    load_manifest,
    preview_manifest,
    save_manifest,
)
from services.planning import Planner


class EnvironmentWorkflow:
    def __init__(self, planner: Planner, config: ProjectConfigService) -> None:
        self.planner = planner
        self.config = config

    @staticmethod
    def _active(cancel: Event) -> None:
        if cancel.is_set():
            raise AdapterError(
                ErrorCode.CANCELLED,
                "The environment task was cancelled before applying changes.",
            )

    def import_manifest(self, path: Path, cancel: Event) -> Manifest:
        self._active(cancel)
        return load_manifest(path)

    def export_manifest(self, manifest: Manifest, path: Path, cancel: Event) -> Path:
        self._active(cancel)
        save_manifest(manifest, path)
        return path

    def preview(self, manifest: Manifest, cancel: Event) -> ManifestPreview:
        return preview_manifest(manifest, self.planner, cancel=cancel)

    def apply_config(self, plan: ConfigPlan, *, confirmed: bool, cancel: Event) -> Path:
        self._active(cancel)
        return self.config.apply(plan, confirmed=confirmed)
