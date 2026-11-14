"""Independent page interaction checks; no MainWindow or native processes needed."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QDialog, QWidget
except ModuleNotFoundError as error:
    if error.name is None or not error.name.startswith("PySide6"):
        raise
    QT_AVAILABLE = False
else:
    from app.ui.change_review import ChangeReview
    from app.ui.components import scroll_page
    from app.ui.navigation import PAGE_NAMES, DesktopShell
    from app.ui.pages.dashboard import DashboardPage
    from app.ui.pages.explore import ExplorePage
    from app.ui.pages.history import HistoryPage
    from app.ui.pages.installed import InstalledPage
    from app.ui.pages.presets import PresetsPage
    from app.ui.pages.queue import QueuePage
    from app.ui.pages.runtimes import RuntimesPage
    from app.ui.pages.settings import SettingsPage
    from app.ui.pages.updates import UpdatesPage

    QT_AVAILABLE = True

from runtimes.adapters import default_adapters
from tests.fixtures.desktop import DesktopFixture
from tests.paths import PROJECT_ROOT


@unittest.skipUnless(QT_AVAILABLE, "Install requirements-gui.txt for page tests.")
class PageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.fixture = DesktopFixture(Path(self.directory.name))
        self.widgets = []

    def show(self, widget, width=900, height=650):
        self.widgets.append(widget)
        widget.resize(width, height)
        widget.show()
        self.app.processEvents()
        return widget

    def tearDown(self):
        for widget in reversed(self.widgets):
            widget.close()
            widget.deleteLater()
        self.app.processEvents()

    def test_every_page_constructs_without_a_window_or_native_work(self):
        services = self.fixture.services
        for constructor in (
            lambda: DashboardPage(services),
            lambda: ExplorePage(services),
            InstalledPage,
            UpdatesPage,
            QueuePage,
            HistoryPage,
            lambda: SettingsPage(services),
            PresetsPage,
        ):
            page = self.show(constructor())
            self.assertIsNone(page.parent())
            self.assertFalse(page.grab().isNull())
        runtime = self.show(RuntimesPage(default_adapters(services.host)))
        self.assertGreater(runtime.manager.count(), 0)
        self.fixture.runner.run.assert_not_called()
        self.assertFalse(self.fixture.threads)

    def test_keyboard_navigation_changes_heading_and_page(self):
        shell = self.show(DesktopShell())
        for _ in PAGE_NAMES:
            shell.stack.addWidget(QWidget())
        shell.navigation.setFocus()
        QTest.keyClick(shell.navigation, Qt.Key.Key_End)
        self.assertEqual(shell.stack.currentIndex(), len(PAGE_NAMES) - 1)
        self.assertEqual(shell.heading.text(), "Runtimes")
        QTest.keyClick(shell.navigation, Qt.Key.Key_Up)
        self.assertEqual(shell.heading.text(), "Presets")
        self.assertEqual(shell.navigation.accessibleName(), "Pages")

    def test_explore_keyboard_focus_and_long_plain_text_survive_resize(self):
        page = self.show(ExplorePage(self.fixture.services))
        page.search.setFocus()
        QTest.keyClicks(page.search, "python")
        self.assertEqual(page.search.text(), "python")
        QTest.keyClick(page.search, Qt.Key.Key_Tab)
        self.assertTrue(page.category.hasFocus())
        self.assertEqual(page.category.accessibleName(), "Package category")
        details = "<script>untrusted text</script>\n" + "long native ID " * 1000
        page.package_details.setPlainText(details)
        for size in ((940, 650), (1500, 1000)):
            page.resize(*size)
            self.app.processEvents()
            self.assertEqual(page.package_details.toPlainText(), details)
            self.assertTrue(page.package_details.isReadOnly())
            self.assertGreater(page.package_details.viewport().width(), 100)
        page.package_details.setFocus()
        QTest.keyClick(page.package_details, Qt.Key.Key_PageDown)
        self.app.processEvents()
        bar = page.package_details.verticalScrollBar()
        self.assertGreater(bar.value(), 0)

    def test_scrollable_settings_and_runtime_controls_remain_reachable(self):
        runtime = RuntimesPage(default_adapters(self.fixture.services.host))
        settings = SettingsPage(self.fixture.services, persistent=True)
        for page, target in (
            (runtime, runtime.runtime_output),
            (settings, settings.support.check),
        ):
            scroll = self.show(scroll_page(page), 720, 400)
            scroll.ensureWidgetVisible(target)
            QTest.keyClick(scroll.verticalScrollBar(), Qt.Key.Key_End)
            self.app.processEvents()
            self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
            position = target.mapTo(scroll.viewport(), target.rect().center())
            self.assertTrue(scroll.viewport().rect().contains(position))
            scroll.resize(1100, 850)
            self.app.processEvents()
            self.assertFalse(scroll.grab().isNull())

    def test_keyboard_review_defaults_to_cancel_and_requires_consent(self):
        details = "Review this exact file change\n" * 300
        dialog = self.show(ChangeReview("Review changes", details))
        self.assertFalse(dialog.run_button.isEnabled())
        self.assertEqual(dialog.details.accessibleName(), "Exact changes to review")
        self.assertEqual(dialog.details.toPlainText(), details)
        QTest.keyClick(dialog, Qt.Key.Key_Return)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertFalse(dialog.isVisible())
        dialog.show()
        dialog.approve.setFocus()
        QTest.keyClick(dialog.approve, Qt.Key.Key_Space)
        self.assertTrue(dialog.run_button.isEnabled())
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)

    def test_high_dpi_pages_render_and_scroll_in_a_fresh_qt_process(self):
        script = """
import tempfile
from pathlib import Path
from PySide6.QtWidgets import QApplication
from app.ui.components import scroll_page
from app.ui.pages.runtimes import RuntimesPage
from runtimes.adapters import default_adapters
from tests.fixtures.desktop import DesktopFixture
app = QApplication([])
with tempfile.TemporaryDirectory() as directory:
    fixture = DesktopFixture(Path(directory))
    page = RuntimesPage(default_adapters(fixture.services.host))
    scroll = scroll_page(page)
    scroll.resize(720, 400)
    scroll.show()
    app.processEvents()
    assert scroll.devicePixelRatioF() == 2.0
    image = scroll.grab()
    assert image.width() == scroll.width() * 2
    assert image.height() == scroll.height() * 2
    scroll.ensureWidgetVisible(page.runtime_output)
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
    app.processEvents()
    position = page.runtime_output.mapTo(scroll.viewport(), page.runtime_output.rect().center())
    assert scroll.viewport().rect().contains(position)
    assert page.project.accessibleName() == 'Project directory'
    fixture.runner.run.assert_not_called()
    scroll.close()
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=PROJECT_ROOT,
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_SCALE_FACTOR": "2"},
            capture_output=True,
            text=True,
            timeout=30,
            shell=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
