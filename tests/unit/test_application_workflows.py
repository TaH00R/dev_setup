"""File workflows share cancellation and service validation without importing Qt."""

import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest import mock

from application.environments import EnvironmentWorkflow
from models.errors import ErrorCode
from package_managers.base import AdapterError
from services.environments import ProjectConfigService, starter_presets
from services.planning import Planner


class EnvironmentWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.planner = mock.create_autospec(Planner, instance=True)
        self.config = ProjectConfigService()
        self.workflow = EnvironmentWorkflow(self.planner, self.config)
        self.cancel = Event()
        self.manifest = starter_presets()[0]

    def test_portable_file_roundtrip_refuses_overwrite(self):
        path = self.root / "environment.json"
        self.assertEqual(
            self.workflow.export_manifest(self.manifest, path, self.cancel), path
        )
        self.assertEqual(
            self.workflow.import_manifest(path, self.cancel), self.manifest
        )
        content = path.read_bytes()
        with self.assertRaises(FileExistsError):
            self.workflow.export_manifest(self.manifest, path, self.cancel)
        self.assertEqual(path.read_bytes(), content)
        self.planner.preview.assert_not_called()

    def test_cancelled_file_tasks_never_read_or_write(self):
        self.cancel.set()
        path = self.root / "environment.json"
        plan = self.config.preview(self.root, "python", "3.13.2")
        for task in (
            lambda: self.workflow.import_manifest(path, self.cancel),
            lambda: self.workflow.export_manifest(self.manifest, path, self.cancel),
            lambda: self.workflow.apply_config(
                plan, confirmed=True, cancel=self.cancel
            ),
        ):
            with self.assertRaises(AdapterError) as caught:
                task()
            self.assertEqual(caught.exception.error.code, ErrorCode.CANCELLED)
        self.assertFalse(path.exists())
        self.assertFalse(plan.path.exists())

    def test_project_workflow_retains_exact_consent_and_stale_plan_checks(self):
        plan = self.config.preview(self.root, "python", "3.13.2")
        with self.assertRaises(ValueError):
            self.workflow.apply_config(plan, confirmed=False, cancel=self.cancel)
        self.assertFalse(plan.path.exists())
        self.workflow.apply_config(plan, confirmed=True, cancel=self.cancel)
        self.assertEqual(plan.path.read_text(), "3.13.2\n")
        plan.path.write_text("3.12.0\n")
        with self.assertRaises(ValueError):
            self.workflow.apply_config(plan, confirmed=True, cancel=self.cancel)
        self.assertEqual(plan.path.read_text(), "3.12.0\n")
