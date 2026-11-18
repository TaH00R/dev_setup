import asyncio
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from textual.widgets import (
    Button,
    Checkbox,
    ContentSwitcher,
    DataTable,
    Input,
    Select,
)

from app.tui.app import DevSetupApp
from app.tui.pages import PAGES
from app.tui.review import Review, plain
from models.errors import ErrorCode
from models.inventory import InstalledPackage, Inventory
from models.planning import OperationAction
from package_managers.base import AdapterError
from persistence.store import StateStore
from services.desktop import DesktopServices
from services.environments import load_manifest
from services.inventory import InventorySource
from services.records import PackageRequest
from tests.fixtures.desktop import DesktopFixture


class TerminalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.fixture = DesktopFixture(self.root)
        detector = mock.patch("services.inventory.shutil.which", return_value=None)
        detector.start()
        self.addCleanup(detector.stop)
        self.app = DevSetupApp(
            self.fixture.services,
            state_path=self.root / "state.sqlite3",
            auto_refresh=False,
        )
        self.context = self.app.run_test(size=(130, 55))
        self.pilot = await self.context.__aenter__()
        await self.idle()

    async def asyncTearDown(self):
        self.fixture.release.set()
        self.app.cancel_event.set()
        await self.idle()
        self.app.action_request_quit()
        await self.pilot.pause()
        await self.context.__aexit__(None, None, None)
        self.directory.cleanup()

    async def idle(self):
        for _ in range(200):
            await self.pilot.pause(0.01)
            if not self.app.busy:
                break
        self.assertFalse(self.app.busy, "Background work did not stop")

    async def click(self, identity):
        widget = self.app.screen.query_one(identity)
        widget.focus(scroll_visible=False)
        widget.scroll_visible(immediate=True)
        await self.pilot.pause()
        await self.pilot.wait_for_scheduled_animations()
        self.assertTrue(await self.pilot.click(identity, offset=(2, 1)))
        await self.pilot.pause()

    async def consent(self, *, elevation=False):
        await self.pilot.pause()
        self.assertIsInstance(self.app.screen, Review)
        self.assertTrue(self.app.screen.query_one("#approve", Button).disabled)
        await self.click("#consent")
        if elevation:
            self.assertTrue(self.app.screen.query_one("#approve", Button).disabled)
            await self.click("#elevation")
        self.assertFalse(self.app.screen.query_one("#approve", Button).disabled)
        await self.click("#approve")

    async def test_default_dark_navigation_and_small_terminal(self):
        self.assertEqual(self.app.theme, "textual-dark")
        self.assertEqual(self.app.query_one("#catalog", DataTable).row_count, 14)
        for page in PAGES:
            self.app.navigate(page)
            await self.pilot.pause()
            self.assertEqual(
                self.app.query_one("#pages", ContentSwitcher).current, page.lower()
            )
        await self.pilot.resize_terminal(80, 24)
        self.app.action_search()
        await self.pilot.pause()
        self.assertEqual(self.app.focused.id, "search")

    async def test_installed_page_shows_native_failure_and_independent_path_tools(self):
        from unittest import mock

        from textual.widgets import Static

        def unavailable(cancel):
            raise AdapterError(
                ErrorCode.MISSING_BACKEND, "Example manager is not installed."
            )

        self.app.services.inventory.sources = (
            InventorySource("missing", unavailable),
            InventorySource(
                "custom",
                lambda cancel: Inventory(
                    "custom", (InstalledPackage("registered-app", ("1.2",)),)
                ),
            ),
        )
        with mock.patch(
            "services.inventory.shutil.which",
            side_effect=lambda name: str(self.root / "git") if name == "git" else None,
        ):
            self.app.action_refresh()
            await self.idle()
        self.app.navigate("Installed")
        await self.pilot.pause()
        table = self.app.query_one("#installed-table", DataTable)
        self.assertEqual(table.row_count, 2)
        self.assertIn("executable", str(table.get_row_at(1)))
        self.assertIn("PATH observation", str(table.get_row_at(1)))
        self.assertIn(
            "Example manager is not installed",
            str(self.app.query_one("#inventory-status", Static).render()),
        )

    async def test_search_filters_preserve_curated_identity_without_installing(self):
        self.app.navigate("Explore")
        self.app.session.settings = replace(
            self.app.session.settings, live_search=False
        )
        self.app.query_one("#search", Input).value = "git"
        await self.pilot.pause(0.4)
        self.assertIn("p-git", self.app.search_rows)
        self.assertEqual(self.fixture.mutations, [])
        self.app.query_one("#category", Select).value = "editors"
        self.app.query_one("#search", Input).value = ""
        await self.pilot.pause(0.4)
        self.assertNotIn("p-git", self.app.search_rows)

    async def test_declining_plan_never_dispatches(self):
        self.app.preview((PackageRequest("git"),))
        await self.idle()
        self.assertIsInstance(self.app.screen, Review)
        await self.pilot.press("escape")
        self.assertEqual(self.fixture.mutations, [])

    async def test_combined_plan_runs_off_ui_thread_and_persists_verified_history(self):
        self.app.preview((PackageRequest("git"), PackageRequest("cmake")))
        await self.idle()
        await self.consent()
        await self.idle()
        self.assertEqual(len(self.fixture.mutations), 2)
        self.assertTrue(all(t != threading.get_ident() for t in self.fixture.threads))
        self.assertEqual(len(self.app.session.history), 2)
        self.assertEqual(self.app.query_one("#history-table", DataTable).row_count, 2)
        store = StateStore(self.root / "state.sqlite3")
        try:
            self.assertEqual(len(store.load_history()), 2)
        finally:
            store.close()

    async def test_blocked_backend_plan_cannot_be_approved(self):
        f = self.fixture
        services = DesktopServices(
            f.services.catalog, f.services.host, (f.adapter,), (f,), f.runner
        )
        plan = services.planner.preview((PackageRequest("git"),))
        self.app._package_plan(plan)
        await self.pilot.pause()
        self.assertTrue(self.app.screen.query_one("#consent", Checkbox).disabled)
        self.assertTrue(self.app.screen.query_one("#approve", Button).disabled)
        await self.pilot.press("escape")
        self.assertEqual(f.mutations, [])

    async def test_separate_elevation_approval_is_required(self):
        self.app._show_review(
            "Permission review",
            "Native administrator approval is required.",
            lambda decision: None,
            elevation=True,
        )
        await self.pilot.pause()
        await self.consent(elevation=True)

    async def test_cancel_remains_responsive_and_interrupted_work_is_inspectable(self):
        self.fixture.block = True
        self.app.preview((PackageRequest("git"),))
        await self.idle()
        await self.consent()
        for _ in range(100):
            if self.fixture.entered.is_set():
                break
            await self.pilot.pause(0.02)
        self.assertTrue(self.fixture.entered.is_set())
        self.app.navigate("Settings")
        await self.pilot.pause()
        self.app.action_cancel()
        await self.idle()
        self.assertEqual(self.app.session.history[0].state, "cancelled")
        self.assertTrue(self.app.recovery_rows)
        self.app.navigate("Queue")
        await self.click("#recovery-inspect")
        await self.idle()
        record = next(iter(self.app.recovery_rows.values()))
        self.assertEqual(record.verification, "matches_before")
        await self.click("#recovery-ack")
        await self.consent()
        await self.idle()
        self.assertFalse(self.app.recovery_rows)

    async def test_project_config_is_previewed_then_written_after_consent(self):
        project = self.root / "project"
        project.mkdir()
        self.app.navigate("Runtimes")
        self.app.query_one("#project-path", Input).value = str(project)
        self.app.query_one("#config-version", Input).value = "3.14.0"
        await self.click("#config-preview")
        await self.idle()
        self.assertFalse((project / ".python-version").exists())
        await self.consent()
        await self.idle()
        self.assertEqual((project / ".python-version").read_text().strip(), "3.14.0")

    async def test_manifest_export_import_and_runtime_choices_round_trip(self):
        self.app.navigate("Presets")
        self.app.query_one("#collection-name", Input).value = "My tools"
        self.app.query_one("#collection-packages", Input).value = "git,cmake"
        target = self.root / "tools.json"
        self.app.query_one("#manifest-path", Input).value = str(target)
        await self.click("#manifest-export")
        self.assertFalse(target.exists())
        await self.consent()
        await self.idle()
        self.assertEqual(
            tuple(p.package_id for p in load_manifest(target).packages),
            ("git", "cmake"),
        )
        await self.click("#manifest-import")
        await self.idle()
        self.assertEqual(self.app.manifest.name, "My tools")
        self.app.navigate("Runtimes")
        self.app.query_one("#runtime-version", Input).value = "3.14.0"
        await self.click("#runtime-add")
        self.assertEqual(self.app.manifest.runtimes[0].version, "3.14.0")

    async def test_preferences_are_persisted_and_diagnostics_export_requires_review(
        self,
    ):
        self.app.navigate("Settings")
        self.app.query_one("#theme-choice", Select).value = "light"
        await self.click("#settings-save")
        await self.idle()
        self.assertEqual(self.app.theme, "textual-light")
        self.assertEqual(self.app.session.store.load_settings().theme, "light")
        await self.click("#diagnostics-preview")
        await self.idle()
        target = self.root / "diagnostics.json"
        self.app.query_one("#diagnostics-path", Input).value = str(target)
        await self.click("#diagnostics-export")
        self.assertFalse(target.exists())
        await self.consent()
        await self.idle()
        self.assertTrue(target.exists())

    async def test_literal_terminal_metadata_cannot_inject_ansi_controls(self):
        self.assertNotIn("\x1b", plain("\x1b[31mmetadata\x1b]8;;https://example.com\a"))
        self.assertEqual(plain("[bold]hello"), "[bold]hello")

    async def test_stale_live_search_is_cancelled_and_provenance_is_retained(self):
        from services.discovery import DiscoveryService
        from tests.fixtures.discovery import FakeProvider

        provider = FakeProvider(self.fixture.services.host)
        provider.block_queries.add("old")
        provider.rows["new"] = (
            provider.hit("native-new", source="reviewed-test-repository"),
        )
        self.app.services.discovery = DiscoveryService(
            self.app.services.catalog, self.app.services.host, (provider,)
        )
        self.app.navigate("Explore")
        self.app.query_one("#search", Input).value = "old"
        for _ in range(100):
            await self.pilot.pause(0.01)
            if provider.started.is_set():
                break
        self.assertTrue(provider.started.is_set())
        self.app.query_one("#search", Input).value = "new"
        for _ in range(150):
            await self.pilot.pause(0.01)
            if not self.app.search_events and "new" in provider.calls:
                break
        live = tuple(
            value
            for value in self.app.search_rows.values()
            if not isinstance(value, str)
        )
        self.assertEqual(live[0].native_id, "native-new")
        self.assertEqual(live[0].source_id, "reviewed-test-repository")
        self.assertFalse(live[0].installable)
        self.assertTrue(all(t != threading.get_ident() for t in provider.thread_ids))

    async def test_runtime_install_uses_exact_consent_and_verifies_native_inventory(
        self,
    ):
        import json

        from models.planning import utc_now
        from runtimes.adapters import Uv
        from runtimes.service import RuntimeService
        from system.commands import CommandResult

        executable = self.root / "uv"
        executable.write_bytes(b"Synthetic runtime manager; never executed")
        manager = Uv(self.fixture.services.host, find=lambda name: str(executable))
        installed = []
        mutations = []

        def run(command, **kwargs):
            if command.mutates:
                self.assertTrue(kwargs["approval"].permits(command))
                mutations.append(command)
                installed.append(
                    {
                        "key": "cpython-3.14.0-macos-aarch64-none",
                        "path": "managed/python",
                    }
                )
            now = utc_now()
            return CommandResult(command.preview(), now, now, 0, json.dumps(installed))

        self.fixture.runner.run.side_effect = run
        self.app.session.runtimes = RuntimeService(
            self.fixture.services.host, self.fixture.runner, (manager,)
        )
        self.app.session.runtimes.lifecycle_store = self.app.session.store
        self.app.navigate("Runtimes")
        self.app.query_one("#runtime-manager", Select).value = "uv"
        self.app.query_one("#runtime-version", Input).value = "3.14.0"
        await self.click("#runtime-preview")
        await self.idle()
        self.assertFalse(mutations)
        await self.consent()
        await self.idle()
        self.assertEqual(len(mutations), 1)
        self.assertEqual(mutations[0].argv[-1], "3.14.0")
        self.assertEqual(self.app.session.records()[-1].state.value, "succeeded")

    async def test_quit_requests_cancellation_and_waits_for_owned_work(self):
        self.fixture.block = True
        self.app.preview((PackageRequest("git"),))
        await self.idle()
        await self.consent()
        for _ in range(100):
            if self.fixture.entered.is_set():
                break
            await self.pilot.pause(0.01)
        self.assertTrue(self.fixture.entered.is_set())
        self.app.action_request_quit()
        self.assertTrue(self.app.cancel_event.is_set())
        await self.idle()
        self.assertEqual(self.app.session.history[0].state, "cancelled")

    async def test_storage_failure_blocks_mutations_but_keeps_catalog_usable(self):
        from threading import Event
        from unittest import mock

        from application.session import Session
        from package_managers.base import AdapterError
        from services.records import PlanConsent

        session = Session(self.fixture.services, self.root / "blocked.sqlite3")
        with mock.patch(
            "application.session.StateStore", side_effect=ValueError("private details")
        ):
            await asyncio.to_thread(session.open)
        self.assertIn("unavailable", session.warning)
        self.assertNotIn("private details", session.warning)
        plan = self.fixture.services.planner.preview((PackageRequest("git"),))
        with self.assertRaises(AdapterError):
            self.fixture.services.operations.execute(
                plan, consent=PlanConsent(plan, True), cancel=Event()
            )
        self.assertFalse(self.fixture.mutations)

    async def test_explore_buttons_build_and_remove_real_queue_requests(self):
        self.app.navigate("Explore")
        table = self.app.query_one("#catalog", DataTable)
        table.move_cursor(row=table.get_row_index("p-git"))
        await self.pilot.pause()
        self.assertEqual(self.app.selection, "git")
        await self.click("#add-install")
        self.assertEqual(self.app.cart["git"].backend, "homebrew")
        self.app.navigate("Queue")
        await self.click("#queue-review")
        await self.idle()
        self.assertIsInstance(self.app.screen, Review)
        await self.pilot.press("escape")
        await self.click("#queue-remove")
        self.assertFalse(self.app.cart)
        self.assertFalse(self.fixture.mutations)

    async def test_partial_results_preserve_selected_source_and_exact_version(self):
        self.app.navigate("Explore")
        table = self.app.query_one("#catalog", DataTable)
        table.move_cursor(row=table.get_row_index("p-git"))
        await self.pilot.pause()
        self.app.query_one("#package-version", Input).value = "2.0"
        source = self.app.query_one("#source", Select).value
        self.app._render_search(self.app.services.discovery.initial("", enabled=False))
        await self.pilot.pause()
        self.assertEqual(self.app.selection, "git")
        self.assertEqual(self.app.query_one("#source", Select).value, source)
        self.assertEqual(self.app.query_one("#package-version", Input).value, "2.0")
        await self.click("#add-install")
        self.assertEqual(self.app.cart["git"].version.value, "2.0")

    async def test_installed_updates_and_verification_use_core_workflows(self):
        self.fixture.install("git", "1.0")
        self.app.action_refresh()
        await self.idle()
        self.assertEqual(self.app.query_one("#installed-table", DataTable).row_count, 1)
        self.app.navigate("Updates")
        await self.click("#tool-updates")
        await self.idle()
        self.assertEqual(self.app.query_one("#updates-table", DataTable).row_count, 1)
        await self.click("#review-updates")
        await self.idle()
        await self.consent()
        await self.idle()
        self.assertEqual(self.fixture.installed["git"].versions, ("2.0",))
        self.assertEqual(self.app.session.history[0].action, "upgrade")
        count = len(self.fixture.mutations)
        self.app.preview((PackageRequest("git", OperationAction.VERIFY),))
        await self.idle()
        await self.consent()
        await self.idle()
        self.assertEqual(len(self.fixture.mutations), count)
        self.assertEqual(self.app.session.history[0].state, "succeeded")
