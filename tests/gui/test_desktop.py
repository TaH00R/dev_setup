"""User-facing desktop acceptance checks with mocked native operations."""

import os
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import QEventLoop, Qt, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog

except ModuleNotFoundError as error:
    if error.name is None or not error.name.startswith("PySide6"):
        raise
    QT_AVAILABLE = False
else:
    from app.ui.main_window import MainWindow
    from app.ui.review import ReviewDialog
    from app.ui.workers import Controller, Job

    QT_AVAILABLE = True

from models.errors import ErrorCode
from models.operations import OperationState, ProgressEvent
from models.package import Dependency
from models.specs import PrivilegeRequirement
from services.catalog.service import CatalogService
from services.planning import Planner
from services.records import PackageRequest
from system.commands import OutputSnapshot
from tests.fixtures.desktop import DesktopFixture


@unittest.skipUnless(
    QT_AVAILABLE, "Install requirements-gui.txt to run desktop acceptance tests."
)
class DesktopTests(unittest.TestCase):
    def test_queue_and_log_limits_keep_selection_consistent_after_eviction(self):
        for index in range(110):
            operation_id = f"operation-{index}"
            self.window._event(
                ProgressEvent(operation_id, "git", OperationState.QUEUED, "Queued")
            )
            self.window._output(operation_id, OutputSnapshot("stdout", operation_id))
        self.assertEqual(self.window.queue_table.rowCount(), 100)
        self.assertEqual(len(self.window.log_cache), 100)
        self.window.queue_table.selectRow(0)
        self.assertIn("operation-10", self.window.log_view.toPlainText())
        self.assertNotIn("operation-0\n", self.window.log_view.toPlainText())

    def test_worker_coalesces_noisy_output_and_bounds_each_snapshot(self):
        job = Job(lambda cancel, event, output: None, self.window)
        notifications = []
        job.output_ready.connect(lambda: notifications.append(True))
        for index in range(1000):
            job.publish_output("operation", OutputSnapshot("stdout", str(index)))
        job.publish_output("operation", OutputSnapshot("stdout", "x" * 70000))
        self.assertEqual(len(notifications), 1)
        snapshot = job.take_output()[0][1]
        self.assertEqual(len(snapshot.text), 65536)
        self.assertTrue(snapshot.truncated)
        self.assertFalse(job.take_output())
        job.deleteLater()

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.fixture = DesktopFixture(Path(self.directory.name))
        self.detection_patch = mock.patch(
            "services.inventory.shutil.which", return_value=None
        )
        self.detection_patch.start()
        self.addCleanup(self.detection_patch.stop)
        self.window = MainWindow(self.fixture.services, auto_refresh=False)
        self.window.show()
        self.app.processEvents()
        self.timers = []

    def tearDown(self):
        for timer in self.timers:
            timer.stop()
        for widget in self.app.topLevelWidgets():
            if isinstance(widget, QDialog):
                widget.reject()
        self.fixture.release.set()
        self.window.controller.cancel()
        self.wait_for(lambda: not self.window.controller.busy)
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.directory.cleanup()

    def wait_for(self, predicate, timeout=5000):
        if predicate():
            return
        loop = QEventLoop()
        poll = QTimer()
        poll.setInterval(10)
        poll.timeout.connect(lambda: loop.quit() if predicate() else None)
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(loop.quit)
        poll.start()
        deadline.start(timeout)
        loop.exec()
        poll.stop()
        deadline.stop()
        self.assertTrue(predicate(), "Timed out waiting for desktop state.")

    def choose_git(self):
        self.window.navigation.setCurrentRow(1)
        self.window.search.setText("git")
        self.wait_for(lambda: self.window.package_list.count() == 2)
        self.window.package_list.setCurrentRow(
            next(n for n, p in enumerate(self.window.matches) if p.id == "git")
        )
        self.assertTrue(self.window.review_button.isEnabled())

    def review_automatically(self, approve=True):
        timer = QTimer(self.window)
        timer.setInterval(10)

        def respond():
            dialog = next(
                (
                    w
                    for w in self.app.topLevelWidgets()
                    if isinstance(w, ReviewDialog) and w.isVisible()
                ),
                None,
            )
            if dialog is None:
                return
            timer.stop()
            if approve:
                if dialog.reviewed.plan.requires_consent:
                    QTest.mouseClick(dialog.approve, Qt.MouseButton.LeftButton)
                if dialog.needs_elevation:
                    QTest.mouseClick(dialog.elevation, Qt.MouseButton.LeftButton)
                if dialog.run_button.isEnabled():
                    QTest.mouseClick(dialog.run_button, Qt.MouseButton.LeftButton)
                else:
                    dialog.reject()
            else:
                dialog.reject()

        timer.timeout.connect(respond)
        self.timers.append(timer)
        timer.start()

    def refresh(self):
        QTest.mouseClick(self.window.refresh_button, Qt.MouseButton.LeftButton)
        self.wait_for(
            lambda: self.window.snapshot is not None and not self.window.controller.busy
        )

    def test_all_navigation_pages_work(self):
        self.assertEqual(self.window.navigation.count(), 9)
        for index, name in enumerate(self.window.pages):
            self.window.navigation.setCurrentRow(index)
            self.assertEqual(self.window.heading.text(), name)
            self.assertEqual(self.window.stack.currentIndex(), index)
        self.assertFalse(self.window.remove_button.isEnabled())

    def test_debounced_search_category_filter_and_package_details(self):
        self.choose_git()
        self.assertIn("https://git-scm.com", self.window.package_details.toPlainText())
        self.assertIn("homebrew-core", self.window.source.currentData().source.id)
        self.assertIn("Privileges", self.window.package_details.toPlainText())
        self.assertFalse(self.window.version.isEnabled())
        self.window.category.setCurrentIndex(self.window.category.findData("editors"))
        self.assertEqual(self.window.package_list.count(), 0)
        self.window.search.clear()
        self.wait_for(lambda: self.window.package_list.count() == 1)
        self.assertEqual(self.window.package.id, "vscode")
        self.assertIn("License", self.window.package_details.toPlainText())
        self.assertFalse(self.fixture.mutations)

    def test_unsupported_platform_mapping_cannot_run(self):
        self.window.search.setText("docker-engine")
        self.wait_for(lambda: self.window.package_list.count() == 1)
        self.assertFalse(self.window.review_button.isEnabled())
        self.assertIn("No reviewed mapping", self.window.action_note.text())
        self.window.compatible.setChecked(True)
        self.assertEqual(self.window.package_list.count(), 0)

    def test_review_requires_unchecked_operation_and_elevation_decisions(self):
        packages = tuple(
            replace(
                p,
                install_specs=tuple(
                    replace(s, privilege=PrivilegeRequirement.REQUIRED)
                    if s.backend == "homebrew"
                    else s
                    for s in p.install_specs
                ),
            )
            if p.id == "git"
            else p
            for p in self.fixture.services.catalog.packages
        )
        self.fixture.services.planner = Planner(
            CatalogService(packages), self.fixture.adapter.host, (self.fixture,)
        )
        reviewed = self.fixture.services.planner.preview((PackageRequest("git"),))
        dialog = ReviewDialog(reviewed, self.window)
        dialog.show()
        self.assertFalse(dialog.approve.isChecked())
        self.assertTrue(dialog.needs_elevation)
        self.assertFalse(dialog.elevation.isChecked())
        self.assertFalse(dialog.run_button.isEnabled())
        self.assertIsNone(dialog.consent())
        text = dialog.details.toPlainText()
        self.assertIn("Arguments:", text)
        self.assertIn("homebrew-core", text)
        self.assertIn("Dependencies:", text)
        self.assertIn("Version: 2.0", text)
        QTest.mouseClick(dialog.approve, Qt.MouseButton.LeftButton)
        if dialog.needs_elevation:
            self.assertFalse(dialog.run_button.isEnabled())
            QTest.mouseClick(dialog.elevation, Qt.MouseButton.LeftButton)
        QTest.mouseClick(dialog.run_button, Qt.MouseButton.LeftButton)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(dialog.consent().reviewed, reviewed)
        self.assertFalse(self.fixture.mutations)
        dialog.deleteLater()

    def test_queue_shows_waiting_dependencies_and_cancels_remaining_items(self):
        packages = tuple(
            replace(p, dependencies=(Dependency("git"),)) if p.id == "nodejs" else p
            for p in self.fixture.services.catalog.packages
        )
        self.fixture.services.planner = Planner(
            CatalogService(packages), self.fixture.adapter.host, (self.fixture,)
        )
        self.fixture.block = True
        self.review_automatically()
        self.window.controller.preview(PackageRequest("nodejs"))
        self.wait_for(self.fixture.entered.is_set)
        self.assertEqual(self.window.queue_table.rowCount(), 2)
        self.assertEqual(self.window.queue_table.item(1, 1).text(), "queued")
        self.window.controller.cancel()
        self.wait_for(lambda: not self.window.controller.busy)
        self.assertEqual(self.window.queue_table.item(1, 1).text(), "cancelled")

    def test_unavailable_backend_is_explained_without_guessing_installability(self):
        self.fixture.services.inspectors = ()
        self.window.package_list.setCurrentRow(
            next(n for n, p in enumerate(self.window.matches) if p.id == "git")
        )
        self.assertFalse(self.window.review_button.isEnabled())
        self.assertIn("not available in this version", self.window.action_note.text())
        self.assertFalse(self.fixture.mutations)

    def test_rejecting_review_never_starts_native_operation(self):
        self.choose_git()
        self.review_automatically(False)
        QTest.mouseClick(self.window.review_button, Qt.MouseButton.LeftButton)
        self.wait_for(
            lambda: "No package changes were started" in self.window.status.text()
        )
        self.assertFalse(self.fixture.mutations)
        self.assertFalse(self.window.controller.history)

    def test_approved_install_updates_queue_logs_history_and_inventory(self):
        self.choose_git()
        self.review_automatically()
        QTest.mouseClick(self.window.review_button, Qt.MouseButton.LeftButton)
        self.wait_for(
            lambda: (
                len(self.window.controller.history) == 1
                and not self.window.controller.busy
            )
        )
        self.assertEqual(len(self.fixture.mutations), 1)
        self.assertEqual(self.window.queue_table.item(0, 1).text(), "succeeded")
        self.assertIn("safe native output", self.window.log_view.toPlainText())
        self.assertEqual(self.window.history_table.rowCount(), 1)
        self.assertIn("succeeded", self.window.activity.text())
        self.assertIsNotNone(self.window.controller.history[0].approved_at)
        self.refresh()
        self.assertEqual(self.window.installed_table.rowCount(), 1)
        self.assertEqual(self.window.installed_table.item(0, 1).text(), "2.0")

    def test_native_failure_and_verification_failure_are_visible(self):
        self.fixture.failure = ErrorCode.COMMAND_FAILED
        self.choose_git()
        self.review_automatically()
        QTest.mouseClick(self.window.review_button, Qt.MouseButton.LeftButton)
        self.wait_for(
            lambda: (
                len(self.window.controller.history) == 1
                and not self.window.controller.busy
            )
        )
        self.assertEqual(self.window.queue_table.item(0, 1).text(), "failed")
        self.assertIn("command_failed", self.window.queue_table.item(0, 3).text())
        self.fixture.failure = None
        self.fixture.mutate = False
        self.review_automatically()
        self.window.controller.preview(PackageRequest("git"))
        self.wait_for(
            lambda: (
                len(self.window.controller.history) == 2
                and not self.window.controller.busy
            )
        )
        self.assertIn("verification_failed", self.window.queue_table.item(1, 3).text())

    def test_background_operation_keeps_gui_responsive_and_can_cancel(self):
        self.fixture.block = True
        self.choose_git()
        self.review_automatically()
        QTest.mouseClick(self.window.review_button, Qt.MouseButton.LeftButton)
        self.wait_for(self.fixture.entered.is_set)
        self.window.navigation.setCurrentRow(6)
        self.assertEqual(self.window.heading.text(), "Settings")
        self.assertTrue(self.window.cancel_button.isVisible())
        self.assertFalse(self.window.refresh_button.isEnabled())
        self.assertFalse(self.window.controller.refresh())
        QTest.mouseClick(self.window.cancel_button, Qt.MouseButton.LeftButton)
        self.wait_for(lambda: not self.window.controller.busy)
        self.assertEqual(self.window.queue_table.item(0, 1).text(), "cancelled")
        self.assertTrue(
            all(ident != threading.get_ident() for ident in self.fixture.threads)
        )

    def test_closing_waits_for_cancellable_worker_instead_of_destroying_it(self):
        self.fixture.block = True
        self.choose_git()
        self.review_automatically()
        QTest.mouseClick(self.window.review_button, Qt.MouseButton.LeftButton)
        self.wait_for(self.fixture.entered.is_set)
        self.window.close()
        self.assertTrue(self.window.pending_close)
        self.wait_for(
            lambda: not self.window.controller.busy and not self.window.isVisible()
        )

    def test_installed_verification_and_removal_use_review_flow(self):
        self.fixture.install()
        self.refresh()
        self.window.installed_table.selectRow(0)
        self.review_automatically()
        QTest.mouseClick(self.window.verify_button, Qt.MouseButton.LeftButton)
        self.wait_for(
            lambda: (
                len(self.window.controller.history) == 1
                and not self.window.controller.busy
            )
        )
        self.assertFalse(self.fixture.mutations)
        self.window.installed_table.selectRow(0)
        self.review_automatically()
        QTest.mouseClick(self.window.remove_button, Qt.MouseButton.LeftButton)
        self.wait_for(
            lambda: (
                len(self.window.controller.history) == 2
                and not self.window.controller.busy
            )
        )
        self.assertEqual(self.fixture.mutations[0].argv[1], "uninstall")
        self.assertFalse(self.fixture.installed)

    def test_update_scan_is_read_only_and_selected_update_gets_fresh_review(self):
        self.fixture.install()
        self.refresh()
        self.window.navigation.setCurrentRow(3)
        QTest.mouseClick(self.window.check_updates, Qt.MouseButton.LeftButton)
        self.wait_for(
            lambda: (
                self.window.update_report is not None
                and not self.window.controller.busy
            )
        )
        self.assertEqual(self.window.update_table.rowCount(), 1)
        self.assertFalse(self.fixture.mutations)
        self.window.update_table.selectRow(0)
        self.review_automatically(False)
        QTest.mouseClick(self.window.review_update, Qt.MouseButton.LeftButton)
        self.wait_for(
            lambda: "No package changes were started" in self.window.status.text()
        )
        self.assertFalse(self.fixture.mutations)
        self.review_automatically()
        QTest.mouseClick(self.window.review_update, Qt.MouseButton.LeftButton)
        self.wait_for(
            lambda: (
                len(self.window.controller.history) == 1
                and not self.window.controller.busy
            )
        )
        self.assertEqual(self.fixture.mutations[0].argv[1], "upgrade")

    def test_inventory_failure_remains_unknown_and_update_check_reports_limits(self):
        self.fixture.inventory_error = ErrorCode.NETWORK
        self.refresh()
        self.assertIn("Unknown", self.window.inventory_count.text())
        self.assertIn("unavailable", self.window.dashboard_notes.text())
        self.window.controller.updates(self.window.snapshot)
        self.wait_for(
            lambda: (
                self.window.update_report is not None
                and not self.window.controller.busy
            )
        )
        self.assertIn("could not be checked", self.window.update_note.text())
        self.assertFalse(self.fixture.mutations)

    def test_settings_theme_search_delay_and_clear_session_work(self):
        self.window.theme.setCurrentIndex(1)
        self.assertIn("#131c27", self.app.styleSheet())
        self.window.debounce.setValue(600)
        self.assertEqual(self.window.search_timer.interval(), 600)
        self.choose_git()
        self.review_automatically()
        QTest.mouseClick(self.window.review_button, Qt.MouseButton.LeftButton)
        self.wait_for(
            lambda: (
                len(self.window.controller.history) == 1
                and not self.window.controller.busy
            )
        )
        self.window.navigation.setCurrentRow(6)
        QTest.mouseClick(self.window.clear_button, Qt.MouseButton.LeftButton)
        self.assertFalse(self.window.controller.history)
        self.assertFalse(self.window.log_cache)
        self.assertEqual(self.window.queue_table.rowCount(), 0)

    def test_review_with_issues_cannot_grant_consent(self):
        plan = self.fixture.services.planner.preview((PackageRequest("unknown"),))
        dialog = ReviewDialog(plan, self.window)
        self.assertFalse(dialog.run_button.isEnabled())
        self.assertIn("Cannot proceed", dialog.details.toPlainText())
        dialog.reject()
        self.assertIsNone(dialog.consent())
        dialog.deleteLater()

    def test_worker_unexpected_error_is_sanitized_and_delivered_on_gui_thread(self):
        controller = Controller(self.fixture.services, self.window)
        errors = []
        controller.error.connect(
            lambda message: errors.append((message, threading.get_ident()))
        )

        def broken(cancel, event, output):
            raise RuntimeError("password=do-not-expose")

        controller.start("refresh", broken)
        self.wait_for(lambda: bool(errors) and not controller.busy)
        self.assertNotIn("do-not-expose", errors[0][0])
        self.assertEqual(errors[0][1], threading.get_ident())
