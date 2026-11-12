"""Qt discovery acceptance: mocks only, no registry queries or native mutations."""

import os
import tempfile
import threading
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QEventLoop, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
except ModuleNotFoundError as error:
    if error.name is None or not error.name.startswith("PySide6"):
        raise
    QT_AVAILABLE = False
else:
    from app.ui.main_window import MainWindow

    QT_AVAILABLE = True

from models.discovery import SearchState
from models.errors import ErrorCode
from services.discovery import DiscoveryService
from tests.fixtures.desktop import DesktopFixture
from tests.fixtures.discovery import FakeProvider


@unittest.skipUnless(
    QT_AVAILABLE, "Install requirements-gui.txt to test live discovery UI."
)
class LiveSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.fixture = DesktopFixture(Path(self.directory.name))
        self.provider = FakeProvider(self.fixture.services.host)
        self.service = DiscoveryService(
            self.fixture.services.catalog,
            self.fixture.services.host,
            (self.provider,),
            timeout_seconds=1,
        )
        self.fixture.services.discovery = self.service
        self.window = MainWindow(self.fixture.services, auto_refresh=False)
        self.window.show()
        self.app.processEvents()

    def wait_for(self, predicate, timeout=4000):
        if predicate():
            return
        loop = QEventLoop()
        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(lambda: loop.quit() if predicate() else None)
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        timer.start()
        deadline.start(timeout)
        loop.exec()
        timer.stop()
        deadline.stop()
        self.assertTrue(predicate(), "Timed out waiting for search state")

    def tearDown(self):
        self.window.search_timer.stop()
        self.provider.release.set()
        self.window.search_controller.cancel()
        self.window.controller.cancel()
        self.wait_for(
            lambda: (
                not self.window.search_controller.busy
                and not self.window.controller.busy
            )
        )
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def search(self, query="git"):
        self.window.navigation.setCurrentRow(1)
        self.window.search.setText(query)
        self.wait_for(
            lambda: bool(self.provider.calls) and not self.window.search_controller.busy
        )

    def test_curated_results_are_immediate_and_live_results_follow_after_debounce(self):
        self.window.search.setText("git")
        self.assertEqual(self.window.package_list.count(), 2)
        self.assertFalse(self.provider.calls)
        self.wait_for(lambda: len(self.window.live_matches) == 1)
        self.assertEqual(self.window.matches[0].id, "git")
        self.assertEqual(self.window.package_list.count(), 3)
        self.assertTrue(
            all(t != threading.get_ident() for t in self.provider.thread_ids)
        )

    def test_typing_dispatches_only_latest_debounced_query(self):
        QTest.keyClicks(self.window.search, "gith")
        self.assertFalse(self.provider.calls)
        self.wait_for(
            lambda: bool(self.provider.calls) and not self.window.search_controller.busy
        )
        self.assertEqual(self.provider.calls, ["gith"])

    def test_live_details_are_plain_text_and_cannot_start_installation(self):
        self.provider.rows["git"] = (
            self.provider.hit(summary="<b>Untrusted description</b>"),
        )
        self.search()
        self.window.package_list.setCurrentRow(len(self.window.matches))
        self.assertIsNone(self.window.package)
        self.assertIn(
            "Discovery: Live (not curated)", self.window.package_details.toPlainText()
        )
        self.assertIn(
            "<b>Untrusted description</b>", self.window.package_details.toPlainText()
        )
        self.assertIn("demo-tool", self.window.package_details.toPlainText())
        self.assertFalse(self.window.review_button.isEnabled())
        self.assertFalse(self.window.action.isEnabled())
        self.assertFalse(self.window.homepage.isEnabled())
        self.assertFalse(self.fixture.mutations)

    def test_cached_results_and_refresh_are_visible(self):
        self.search()
        self.window._search()
        self.wait_for(
            lambda: (
                self.window.search_controller.latest.providers[0].state
                == SearchState.CACHED
                and not self.window.search_controller.busy
            )
        )
        self.assertEqual(len(self.provider.calls), 1)
        self.assertIn("cached", self.window.search_summary.text())
        QTest.mouseClick(self.window.refresh_live, Qt.MouseButton.LeftButton)
        self.wait_for(
            lambda: (
                len(self.provider.calls) == 2 and not self.window.search_controller.busy
            )
        )

    def test_old_results_are_suppressed_and_retired_job_cannot_bypass_debounce(self):
        controller = self.window.search_controller
        self.provider.block_queries.add("old")
        controller.request("old")
        self.wait_for(self.provider.started.is_set)
        controller.request("new", deferred=True)
        self.wait_for(lambda: not controller.busy)
        self.assertEqual(self.provider.calls, ["old"])
        self.assertEqual(controller.latest.query, "new")
        self.assertFalse(controller.latest.live)
        controller.dispatch()
        self.wait_for(lambda: not controller.busy and controller.latest.complete)
        self.assertEqual(self.provider.calls, ["old", "new"])
        self.assertEqual(controller.latest.query, "new")

    def test_partial_failure_preserves_curated_and_successful_live_source(self):
        other = FakeProvider(self.fixture.services.host, "offline")
        other.error = ErrorCode.NETWORK
        self.window.search_controller.service = DiscoveryService(
            self.fixture.services.catalog,
            self.fixture.services.host,
            (self.provider, other),
        )
        self.search()
        self.assertEqual(len(self.window.matches), 2)
        self.assertEqual(len(self.window.live_matches), 1)
        self.assertIn("offline: failed", self.window.search_summary.text())
        self.assertIn("Synthetic provider failed", self.window.search_summary.text())

    def test_cancellation_and_closing_wait_for_worker_cleanup(self):
        self.window.navigation.setCurrentRow(1)
        self.provider.block_queries.add("git")
        self.window.search.setText("git")
        self.window.search_controller.dispatch()
        self.wait_for(self.provider.started.is_set)
        self.assertTrue(self.window.cancel_search.isVisible())
        self.window.close()
        self.assertTrue(self.window.pending_close)
        self.wait_for(
            lambda: (
                not self.window.search_controller.busy and not self.window.isVisible()
            )
        )

    def test_category_and_live_settings_do_not_query_unrelated_sources(self):
        self.window.live_search_setting.setChecked(False)
        self.window.search.setText("git")
        self.assertFalse(self.window.live_enabled.isChecked())
        self.assertFalse(self.window.search_controller.pending)
        self.assertFalse(self.provider.calls)
        self.window.category.setCurrentIndex(
            self.window.category.findData("api-network")
        )
        self.window.live_enabled.setChecked(True)
        self.assertFalse(self.window.search_controller.pending)
        self.assertIn("Category filters", self.window.search_summary.text())

    def test_partial_updates_preserve_curated_source_action_and_version_input(self):
        self.provider.block_queries.add("git")
        self.window.search.setText("git")
        self.window.action.setCurrentIndex(self.window.action.findText("Verify"))
        self.window.version.setText("1.0")
        self.window.search_controller.dispatch()
        self.wait_for(self.provider.started.is_set)
        self.provider.release.set()
        self.wait_for(lambda: not self.window.search_controller.busy)
        self.assertEqual(self.window.package.id, "git")
        self.assertEqual(self.window.action.currentText(), "Verify")
        self.assertEqual(self.window.version.text(), "1.0")
