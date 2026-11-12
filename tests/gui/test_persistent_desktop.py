"""Desktop persistence and support controls never touch real app data or a network."""

import json
import os
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
except ModuleNotFoundError as error:
    if error.name is None or not error.name.startswith("PySide6"):
        raise
    QT_AVAILABLE = False
else:
    from app.ui.main_window import MainWindow

    QT_AVAILABLE = True

from models.environment import RuntimeAudit, RuntimeResult
from models.operations import OperationState
from models.planning import utc_now
from persistence.store import DesktopSettings, StateError, StateStore
from services.records import AuditRecord, ExecutionReport
from services.updates import UpdateCheck
from tests.fixtures.desktop import DesktopFixture


@unittest.skipUnless(QT_AVAILABLE, "Install requirements-gui.txt for desktop tests.")
class PersistentDesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.path = self.root / "state.sqlite3"
        self.fixture = DesktopFixture(self.root)
        self.windows = []
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        for window in self.windows:
            window.state_controller.flush()
            window.support.controller.cancel()
            self.wait_for(lambda: not window.support.controller.busy)
            window.close()
            self.wait_for(lambda: not window.isVisible())
            window.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def wait_for(self, predicate):
        if predicate():
            return
        loop, timer, deadline = QEventLoop(), QTimer(), QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: loop.quit() if predicate() else None)
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        timer.start()
        deadline.start(5000)
        loop.exec()
        timer.stop()
        deadline.stop()
        self.assertTrue(predicate(), "Timed out waiting for persistent desktop")

    def open_window(self, *, wait=True, persistent=True):
        window = MainWindow(
            self.fixture.services,
            auto_refresh=False,
            state_path=self.path if persistent else None,
        )
        self.windows.append(window)
        window.show()
        if wait:
            self.wait_for(lambda: not window.state_controller.busy)
        return window

    def audit(self, operation_id="operation-1"):
        now = utc_now()
        return AuditRecord(
            operation_id,
            "git",
            "install",
            "homebrew",
            "homebrew-core",
            "2.0",
            "succeeded",
            now,
            now,
            0,
        )

    def test_default_window_remains_ephemeral(self):
        with mock.patch("app.ui.state.StateStore") as constructor:
            window = self.open_window(persistent=False)
            window.theme.setCurrentIndex(1)
            window._completed(ExecutionReport((), (self.audit(),)))
            constructor.assert_not_called()
        self.assertFalse(self.path.exists())

    def test_preferences_and_audit_survive_restart(self):
        window = self.open_window()
        window.theme.setCurrentIndex(1)
        window.debounce.setValue(650)
        window.live_search_setting.setChecked(False)
        record = self.audit()
        window._completed(ExecutionReport((), (record,)))
        self.wait_for(lambda: not window.state_controller.busy)
        window.close()
        second = self.open_window()
        self.assertEqual(second.theme.currentText(), "Dark")
        self.assertEqual(second.search_timer.interval(), 650)
        self.assertFalse(second.live_enabled.isChecked())
        self.assertEqual(tuple(second.controller.history), (record,))
        self.assertEqual(second.history_table.item(0, 3).text(), "succeeded")
        self.assertIn("#131c27", self.app.styleSheet())

    def test_startup_gates_preferences_and_merges_current_session_without_duplicates(
        self,
    ):
        current = self.audit("shared-operation")
        old = replace(
            current,
            state="failed",
            approved_at=None,
            finished_at=current.finished_at - timedelta(days=1),
        )
        older = replace(
            old,
            operation_id="older-operation",
            finished_at=old.finished_at - timedelta(days=1),
        )
        store = StateStore(self.path)
        try:
            store.save_settings(DesktopSettings(live_search=False))
            store.append_audit((older, old))
        finally:
            store.close()
        entered = threading.Event()
        original = StateStore.load_history

        def blocked(store, limit=200):
            entered.set()
            self.release.wait(5)
            return original(store, limit)

        with mock.patch.object(StateStore, "load_history", blocked):
            window = self.open_window(wait=False)
            self.wait_for(entered.is_set)
            self.assertFalse(window.theme.isEnabled())
            self.assertFalse(window.live_enabled.isEnabled())
            window._completed(ExecutionReport((), (current,)))
            self.release.set()
            self.wait_for(lambda: not window.state_controller.busy)
        self.assertEqual(tuple(window.controller.history), (older, current))
        self.assertTrue(window.theme.isEnabled())
        self.assertEqual(window.history_table.item(0, 3).text(), "succeeded")

    def test_database_failure_keeps_successful_operation_visible(self):
        window = self.open_window()
        record = self.audit()
        with mock.patch.object(
            StateStore,
            "append_audit",
            side_effect=StateError("Saved history is unavailable."),
        ):
            window._completed(ExecutionReport((), (record,)))
            self.wait_for(lambda: not window.state_controller.busy)
        self.assertEqual(window.controller.history[-1].state, "succeeded")
        self.assertEqual(window.history_table.item(0, 3).text(), "succeeded")
        self.assertIn("Plan finished", window.status.text())
        self.assertIn("Persistence warning", window.persistence_note.text())

    def test_approved_runtime_audit_uses_same_durable_history_without_output(self):
        window = self.open_window()
        now = utc_now()
        audit = RuntimeAudit(
            "runtime-operation",
            "runtime-plan",
            "uv",
            "python",
            "3.14.0",
            OperationState.SUCCEEDED,
            now,
            now,
            0,
        )
        window.environment._done(
            "runtime-execute",
            RuntimeResult(OperationState.SUCCEEDED, "Runtime verified.", audit=audit),
        )
        self.wait_for(lambda: not window.state_controller.busy)
        record = window.controller.history[-1]
        self.assertEqual(record.package_id, "runtime-python")
        self.assertEqual(record.source_id, "runtime-uv")
        self.assertEqual(record.backend, "uv")
        self.assertEqual(record.state, "succeeded")
        window.close()
        second = self.open_window()
        self.assertEqual(tuple(second.controller.history), (record,))
        self.assertIn("succeeded", window.environment.runtime_status.text())

    def test_saved_history_clear_defaults_to_no_and_requires_explicit_yes(self):
        window = self.open_window()
        window._completed(ExecutionReport((), (self.audit(),)))
        self.wait_for(lambda: not window.state_controller.busy)
        with mock.patch(
            "app.ui.main_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ) as question:
            window._clear()
            self.assertEqual(question.call_args.args[-1], QMessageBox.StandardButton.No)
        self.assertEqual(len(window.controller.history), 1)
        with mock.patch(
            "app.ui.main_window.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            window._clear()
            self.wait_for(lambda: not window.state_controller.busy)
        self.assertFalse(window.controller.history)
        window.close()
        self.assertFalse(self.open_window().controller.history)

    def test_startup_failure_is_recoverable_for_session_use(self):
        self.path.write_bytes(b"not a database")
        window = self.open_window()
        self.assertIn("Persistence warning", window.persistence_note.text())
        self.assertTrue(window.theme.isEnabled())
        window._completed(ExecutionReport((), (self.audit(),)))
        self.assertEqual(window.controller.history[-1].state, "succeeded")

    def test_diagnostics_preview_export_is_exclusive_and_updates_are_manual_disabled(
        self,
    ):
        window = self.open_window(persistent=False)
        self.assertIn("disabled", window.support.note.text())
        with mock.patch(
            "urllib.request.OpenerDirector.open",
            side_effect=AssertionError("No network"),
        ):
            window.support.check_updates()
            self.wait_for(lambda: not window.support.controller.busy)
        self.assertIn("No trusted signed release channel", window.support.note.text())
        window.support.preview_diagnostics()
        self.wait_for(lambda: not window.support.controller.busy)
        path = self.root / "diagnostics.json"
        self.assertTrue(window.support.export_to(path))
        self.wait_for(lambda: not window.support.controller.busy)
        payload = json.loads(path.read_text())
        self.assertEqual(payload["platform"]["os"], "macos")
        original = path.read_bytes()
        window.support.export_to(path)
        self.wait_for(lambda: not window.support.controller.busy)
        self.assertEqual(path.read_bytes(), original)

    def test_update_checks_run_off_gui_thread_and_close_waits_for_cancellation(self):
        window = self.open_window(persistent=False)
        entered = threading.Event()
        threads = []

        def check(version, host, cancel):
            threads.append(threading.get_ident())
            entered.set()
            cancel.wait(5)
            return UpdateCheck("cancelled", "Cancelled check.")

        window.support.updates.check = mock.Mock(side_effect=check)
        window.support.check_updates()
        self.wait_for(entered.is_set)
        self.assertNotEqual(threads[0], threading.main_thread().ident)
        window.close()
        self.wait_for(lambda: not window.support.controller.busy)
        self.wait_for(lambda: not window.isVisible())

    def test_close_waits_for_queued_preference_write(self):
        window = self.open_window()
        entered = threading.Event()
        original = StateStore.save_settings

        def blocked(store, settings):
            entered.set()
            self.release.wait(5)
            original(store, settings)

        with mock.patch.object(StateStore, "save_settings", blocked):
            window.theme.setCurrentIndex(1)
            self.wait_for(entered.is_set)
            window.close()
            self.assertTrue(window.isVisible())
            self.release.set()
            self.wait_for(lambda: not window.state_controller.busy)
            self.wait_for(lambda: not window.isVisible())
        second = self.open_window()
        self.assertEqual(second.theme.currentText(), "Dark")
