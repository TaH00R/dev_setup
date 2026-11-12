"""Environment desktop flows use temporary projects and mocked native work."""

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication, QDialog
except ModuleNotFoundError as error:
    if error.name is None or not error.name.startswith("PySide6"):
        raise
    QT_AVAILABLE = False
else:
    from app.ui.environments import ChangeReview
    from app.ui.main_window import MainWindow

    QT_AVAILABLE = True

from models.environment import (
    EnvironmentChoice,
    Manifest,
    ManifestPackage,
    ProjectConfigChoice,
    RuntimePlan,
    RuntimeRequest,
    RuntimeResult,
)
from models.operations import OperationState
from services.environments import load_manifest, save_manifest, starter_presets
from system.commands import Command, OutputSnapshot
from tests.fixtures.desktop import DesktopFixture


@unittest.skipUnless(QT_AVAILABLE, "Install requirements-gui.txt for desktop tests.")
class EnvironmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.fixture = DesktopFixture(self.root)
        self.window = MainWindow(self.fixture.services, auto_refresh=False)
        self.pages = self.window.environment
        self.window.show()
        self.timers = []
        self.app.processEvents()

    def tearDown(self):
        for timer in self.timers:
            timer.stop()
        for widget in self.app.topLevelWidgets():
            if isinstance(widget, QDialog):
                widget.reject()
        self.fixture.release.set()
        self.pages.controller.cancel()
        self.window.controller.cancel()
        self.wait_for(lambda: not self.pages.controller.busy)
        self.wait_for(lambda: not self.window.controller.busy)
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def wait_for(self, predicate, timeout=5000):
        if predicate():
            return
        loop = QEventLoop()
        poll, deadline = QTimer(), QTimer()
        poll.setInterval(10)
        poll.timeout.connect(lambda: loop.quit() if predicate() else None)
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        poll.start()
        deadline.start(timeout)
        loop.exec()
        poll.stop()
        deadline.stop()
        self.assertTrue(predicate(), "Timed out waiting for environment task.")

    def review(self, approve, inspect=None):
        timer = QTimer(self.window)
        timer.setInterval(10)

        def respond():
            dialog = next(
                (
                    w
                    for w in self.app.topLevelWidgets()
                    if isinstance(w, ChangeReview) and w.isVisible()
                ),
                None,
            )
            if dialog is None:
                return
            timer.stop()
            if inspect:
                inspect(dialog)
            if approve:
                if dialog.approve.isVisible():
                    dialog.approve.setChecked(True)
                dialog.run_button.click()
            else:
                dialog.reject()

        timer.timeout.connect(respond)
        timer.start()
        self.timers.append(timer)

    def runtime_fake(self):
        self.pages.manager.setCurrentIndex(self.pages.manager.findText("uv"))
        self.pages.runtime_version.setText("3.14.0")
        request = RuntimeRequest("uv", "python", "3.14.0")
        command = Command(
            (str(self.root / "uv"), "python", "install", "3.14.0"),
            "Install reviewed Python",
            self.fixture.services.host.os,
            "runtime-uv",
            True,
        )
        plan = RuntimePlan(
            "runtime-test", request, command, (), ("Reviewed manager source",)
        )
        service = mock.Mock()
        service.preview.return_value = plan

        def execute(plan, *, consent, cancel, observe):
            self.assertIs(consent.plan, plan)
            self.assertTrue(consent.confirmed)
            self.assertNotEqual(threading.get_ident(), threading.main_thread().ident)
            observe(OutputSnapshot("stdout", "verified Python"))
            return RuntimeResult(OperationState.SUCCEEDED, "Runtime verified.")

        service.execute.side_effect = execute
        self.pages.runtime_service = service
        return service

    def test_starter_roles_and_session_collection_are_editable(self):
        self.assertEqual(self.pages.presets.count(), 10)
        self.assertEqual(
            tuple(self.pages.presets.itemData(i).name for i in range(10)),
            tuple(preset.name for preset in starter_presets()),
        )
        self.pages.collection_name.setText("My tools")
        self.pages._save_collection()
        self.assertEqual(self.pages.presets.currentText(), "User: My tools")
        self.assertEqual(self.pages.collections["My tools"].name, "My tools")
        self.assertIn("session", self.pages.manifest_status.text())

    def test_import_export_preserves_unsupported_choices_and_refuses_overwrite(self):
        manifest = Manifest(
            "Portable",
            (ManifestPackage("unknown-tool"),),
            (RuntimeRequest("uv", "python", "3.14.0"),),
            configurations=(ProjectConfigChoice("python", "3.14.0"),),
            environment=(EnvironmentChoice("PYTHONPATH", "src"),),
        )
        source, target = self.root / "source.json", self.root / "target.json"
        save_manifest(manifest, source)
        self.assertTrue(self.pages.import_manifest(source))
        self.wait_for(lambda: not self.pages.controller.busy)
        self.assertEqual(self.pages._manifest(), manifest)
        self.assertTrue(self.pages.export_manifest(target))
        self.wait_for(lambda: not self.pages.controller.busy)
        self.assertEqual(load_manifest(target), manifest)
        original = target.read_bytes()
        self.pages.export_manifest(target)
        self.wait_for(lambda: not self.pages.controller.busy)
        self.assertEqual(target.read_bytes(), original)
        self.assertIn("could not be completed", self.pages.manifest_status.text())

    def test_unsupported_preview_blocks_package_operations_and_invalidates_on_edit(
        self,
    ):
        self.pages._load_manifest(
            Manifest("Unknown", (ManifestPackage("unknown-tool"),))
        )
        self.pages._preview_manifest()
        self.wait_for(lambda: not self.pages.controller.busy)
        self.assertIn("Unsupported:", self.pages.manifest_details.toPlainText())
        self.assertFalse(self.pages.package_button.isEnabled())
        self.pages.collection_name.setText("Edited")
        self.assertIsNone(self.pages.preview)

    def test_runtime_rejection_does_not_execute(self):
        service = self.runtime_fake()
        self.review(False)
        self.pages._preview_runtime()
        self.wait_for(lambda: not self.pages.controller.busy)
        service.execute.assert_not_called()
        self.assertIn("No runtime installation", self.pages.runtime_status.text())

    def test_runtime_consent_holds_package_gate_then_executes_off_gui_thread(self):
        service = self.runtime_fake()
        observed = []

        def inspect(dialog):
            observed.append(dialog.details.toPlainText())
            self.assertFalse(dialog.run_button.isEnabled())
            self.assertTrue(self.pages.controller.busy)
            self.assertFalse(
                self.window.controller.start("unexpected", lambda *args: None)
            )

        self.review(True, inspect)
        self.pages._preview_runtime()
        self.wait_for(lambda: not self.pages.controller.busy)
        service.execute.assert_called_once()
        self.assertIn("3.14.0", observed[0])
        self.assertIn("runtime-uv", observed[0])
        self.assertIn("succeeded", self.pages.runtime_status.text())
        self.assertIn("verified Python", self.pages.runtime_output.toPlainText())

    def test_invalid_runtime_and_empty_project_do_not_start_work(self):
        service = self.runtime_fake()
        self.pages.runtime_version.setText("latest")
        self.pages._preview_runtime()
        service.preview.assert_not_called()
        self.pages.config_version.setText("3.14.0")
        self.pages._preview_config()
        self.assertFalse(self.pages.controller.busy)
        self.assertIn("project directory", self.pages.runtime_status.text())

    def test_project_file_requires_consent_and_displays_exact_content(self):
        self.pages.project.setText(str(self.root))
        self.pages.config_version.setText("3.14.0")
        target = self.root / ".python-version"
        self.review(False)
        self.pages._preview_config()
        self.wait_for(lambda: not self.pages.controller.busy)
        self.assertFalse(target.exists())
        observed = []
        self.review(True, lambda dialog: observed.append(dialog.details.toPlainText()))
        self.pages._preview_config()
        self.wait_for(lambda: not self.pages.controller.busy)
        self.assertEqual(target.read_text(), "3.14.0\n")
        self.assertIn("REVIEWED CONTENT\n3.14.0", observed[0])

    def test_package_review_reservation_prevents_runtime_start(self):
        service = self.runtime_fake()
        self.assertTrue(self.window.controller.reserve_review())
        self.pages._preview_runtime()
        service.preview.assert_not_called()
        self.assertFalse(self.pages.controller.busy)
        self.window.controller.release_review()

    def test_cancelled_preview_never_opens_consent_and_close_waits(self):
        entered = threading.Event()

        def task(cancel, event, output):
            entered.set()
            cancel.wait(5)
            return RuntimePlan(
                "cancelled",
                RuntimeRequest("uv", "python", "3.14.0"),
                None,
                (),
                (),
                "Already installed",
            )

        self.pages.controller.start("runtime-preview", task)
        self.wait_for(entered.is_set)
        self.window.close()
        self.wait_for(lambda: not self.pages.controller.busy)
        self.wait_for(lambda: not self.window.isVisible())
        self.assertFalse(
            any(
                isinstance(w, ChangeReview) and w.isVisible()
                for w in self.app.topLevelWidgets()
            )
        )
