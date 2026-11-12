"""Durable state work stays off the GUI thread and has safe failure behavior."""

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as error:
    if error.name is None or not error.name.startswith("PySide6"):
        raise
    QT_AVAILABLE = False
else:
    from app.ui.state import StateController

    QT_AVAILABLE = True

from models.planning import utc_now
from persistence.store import DesktopSettings, StateError
from services.records import AuditRecord


@unittest.skipUnless(
    QT_AVAILABLE, "Install requirements-gui.txt for state worker tests."
)
class StateWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "state.sqlite3"
        self.controller = StateController()

    def tearDown(self):
        self.controller.close()
        self.controller.deleteLater()
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
        deadline.start(4000)
        loop.exec()
        timer.stop()
        deadline.stop()
        self.assertTrue(predicate(), "Timed out waiting for state worker")

    def load(self):
        self.assertTrue(self.controller.startup(self.path))
        self.wait_for(lambda: not self.controller.busy)
        self.assertFalse(self.controller.failure)

    def audit(self):
        return AuditRecord(
            "operation-1",
            "git",
            "install",
            "homebrew",
            "homebrew-core",
            "2.0",
            "succeeded",
            utc_now(),
            utc_now(),
            0,
        )

    def test_event_log_failure_does_not_discard_audit(self):
        self.load()
        record = self.audit()
        with mock.patch.object(
            self.controller.event_log, "record", side_effect=OSError("private path")
        ):
            self.controller.append_audit((record,))
            self.wait_for(lambda: not self.controller.busy)
        self.assertFalse(self.controller.failure)
        self.assertEqual(self.controller.store.load_history(), (record,))

    def test_settings_history_restart_and_clear_are_durable(self):
        self.load()
        desired = DesktopSettings("dark", 600, False)
        self.controller.save_settings(desired)
        self.controller.append_audit((self.audit(),))
        self.wait_for(lambda: not self.controller.busy)
        log = self.path.with_name("events.log").read_text(encoding="utf-8")
        self.assertIn('"event": "state-opened"', log)
        self.assertIn('"code": "audit"', log)
        self.assertNotIn("homebrew-core", log)
        restored = []
        self.controller.loaded.connect(restored.append)
        self.controller.startup(self.path)
        self.wait_for(lambda: bool(restored) and not self.controller.busy)
        self.assertEqual(restored[-1].settings, desired)
        self.assertEqual(restored[-1].history[0].package_id, "git")
        self.controller.clear_history()
        self.wait_for(lambda: not self.controller.busy)
        self.assertFalse(self.controller.store.load_history())

    def test_slow_storage_coalesces_preferences_off_gui_thread(self):
        self.load()
        entered, release = threading.Event(), threading.Event()
        store = self.controller.store
        real_append = store.append_audit
        threads = []

        def slow(records):
            threads.append(threading.get_ident())
            entered.set()
            release.wait(2)
            real_append(records)

        with (
            mock.patch.object(store, "append_audit", side_effect=slow),
            mock.patch.object(
                store, "save_settings", wraps=store.save_settings
            ) as save,
        ):
            self.controller.append_audit((self.audit(),))
            self.wait_for(entered.is_set)
            for delay in range(150, 170):
                self.controller.save_settings(DesktopSettings(search_delay_ms=delay))
            release.set()
            self.wait_for(lambda: not self.controller.busy)
            self.assertEqual(save.call_count, 1)
            self.assertEqual(save.call_args.args[0].search_delay_ms, 169)
        self.assertTrue(all(value != threading.get_ident() for value in threads))

    def test_failure_pauses_writes_and_reports_safe_message(self):
        self.load()
        errors = []
        self.controller.error.connect(errors.append)
        with mock.patch.object(
            self.controller.store,
            "save_settings",
            side_effect=StateError("Synthetic storage denied."),
        ):
            self.controller.save_settings(DesktopSettings())
            self.wait_for(lambda: not self.controller.busy)
        self.assertIn("Synthetic storage denied", errors[0])
        self.assertFalse(self.controller.append_audit((self.audit(),)))
        self.assertTrue(self.controller.startup(self.path))
        self.wait_for(lambda: not self.controller.busy)
        self.assertFalse(self.controller.failure)

    def test_corrupt_existing_database_is_retained_and_load_failure_is_safe(self):
        self.path.write_bytes(b"not a sqlite database")
        self.controller.startup(self.path)
        self.wait_for(lambda: not self.controller.busy)
        self.assertTrue(self.controller.failure)
        self.assertNotIn(str(self.path), self.controller.failure)
        self.assertEqual(self.path.read_bytes(), b"not a sqlite database")
