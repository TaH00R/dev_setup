"""Keyboard-first application shell. Blocking services run in cancellable workers."""

import hashlib
from collections import OrderedDict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from pathlib import Path
from threading import Event, Lock
from typing import Any, TypeVar

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.timer import Timer
from textual.widgets import (
    Button,
    Checkbox,
    ContentSwitcher,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    Select,
    Static,
    TextArea,
)

from application.session import Session
from models.discovery import LivePackage
from models.environment import (
    ConfigPlan,
    Manifest,
    ManifestPackage,
    RuntimePlan,
    RuntimeRequest,
    RuntimeResult,
)
from models.errors import ErrorCode
from models.lifecycle import LifecycleRecord
from models.operations import ProgressEvent
from models.package import PackageVersion
from models.planning import OperationAction
from package_managers.base import AdapterError
from persistence.store import StateError
from services.catalog.schema import CATEGORIES
from services.desktop import DesktopServices, DesktopSnapshot, UpdateReport
from services.diagnostics import (
    diagnostic_json,
)
from services.discovery import SearchSnapshot
from services.environments import (
    ManifestPreview,
    starter_presets,
)
from services.records import ExecutionReport, PackageRequest, ReviewedPlan
from system.commands import OutputSnapshot

from .actions import Actions
from .pages import PAGES, pages
from .presentation import config_review, package_review, runtime_review
from .review import Decision, Review, plain

T = TypeVar("T")


class DevSetupApp(App[None]):
    TITLE = "Dev Setup"
    SUB_TITLE = "Developer tools · reviewed changes · verified results"
    BINDINGS = [
        ("ctrl+q", "request_quit", "Quit"),
        ("ctrl+c", "cancel", "Cancel work"),
        ("f5", "refresh", "Refresh"),
        ("ctrl+f", "search", "Search"),
    ]
    CSS = """
    #layout { height: 1fr; }
    #navigation { width: 18; border-right: solid $primary; height: 1fr; }
    #navigation ListItem { padding: 0 1; }
    ContentSwitcher { width: 1fr; }
    VerticalScroll { padding: 1 2; }
    DataTable { height: 12; margin: 1 0; }
    #details { min-height: 5; margin: 1 0; }
    .actions { height: auto; min-height: 3; }
    Button { margin: 0 1 1 0; min-width: 10; }
    Input, Select { margin-bottom: 1; }
    TextArea { height: 12; margin: 1 0; }
    #status { height: auto; max-height: 3; background: $boost; padding: 0 1; }
    #system, #backends { padding: 1; }
    """

    def __init__(
        self,
        services: DesktopServices,
        *,
        state_path: Path | None = None,
        auto_refresh: bool = True,
    ) -> None:
        super().__init__()
        self.actions = Actions(self)
        self.session = Session(services, state_path)
        self.services = services
        self.refresh_on_start = auto_refresh
        self.theme = "textual-dark"
        self.busy = False
        self.reviewing = False
        self.closing = False
        self.cancel_event = Event()
        self.search_events: dict[int, Event] = {}
        self.search_generation = 0
        self.search_timer: Timer | None = None
        self.snapshot: DesktopSnapshot | None = None
        self.offers: UpdateReport | None = None
        self.selection: str | None = None
        self.search_rows: dict[str, str | LivePackage] = {}
        self.cart: OrderedDict[str, PackageRequest] = OrderedDict()
        self.presets = starter_presets()
        self.manifest = self.presets[0]
        self.recovery_rows: dict[str, LifecycleRecord] = {}
        self.diagnostics = None
        self._log_lock = Lock()
        self._logs: OrderedDict[tuple[str, str], str] = OrderedDict()
        self._logs_dirty = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="layout"):
            yield ListView(*(ListItem(Label(name)) for name in PAGES), id="navigation")
            with ContentSwitcher(initial="dashboard", id="pages"):
                yield from pages()
        yield Static("Loading…", id="status", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        columns = {
            "catalog": ("Tool", "Category", "Origin", "ID"),
            "installed-table": ("Package", "Version", "Backend", "Source"),
            "updates-table": ("Package", "Installed", "Candidate", "Source"),
            "cart": ("Package", "Action", "Backend", "Source"),
            "history-table": ("Package", "Action", "Result", "Backend", "Finished"),
            "recovery-table": ("Package", "State", "Inspection", "Recovery"),
        }
        for identity, headings in columns.items():
            self.query_one("#" + identity, DataTable).add_columns(*headings)
        self.query_one("#category", Select).set_options(
            [("All categories", "all"), *((c, c) for c in sorted(CATEGORIES))]
        )
        self.query_one("#preset", Select).set_options(
            [(p.name, str(i)) for i, p in enumerate(self.presets)]
        )
        self.set_interval(0.15, self._drain_logs)
        self.run_job(
            "Open saved state", lambda cancel: self.session.open(), self._opened
        )

    def status(self, value: object) -> None:
        self.query_one("#status", Static).update(plain(value))

    def navigate(self, name: str) -> None:
        self.query_one("#pages", ContentSwitcher).current = name.lower()
        self.query_one("#navigation", ListView).index = PAGES.index(name.title())

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "navigation" and event.list_view.index is not None:
            self.query_one("#pages", ContentSwitcher).current = PAGES[
                event.list_view.index
            ].lower()

    def _opened(self, value: object) -> None:
        settings = self.session.settings
        self.theme = "textual-dark" if settings.theme == "dark" else "textual-light"
        self.query_one("#theme-choice", Select).value = settings.theme
        self.query_one("#live-search", Checkbox).value = settings.live_search
        self.query_one("#search-delay", Input).value = str(settings.search_delay_ms)
        self._load_manifest(self.manifest)
        self._history()
        self._recovery()
        host = self.services.host
        self.query_one("#system", Static).update(
            plain(
                f"{host.os.value} / {host.architecture.value}\n{host.distribution or ''}\n{len(self.services.catalog.packages)} curated tools\n{self.session.warning}"
            )
        )
        self._render_search(self.services.discovery.initial("", enabled=False))
        self.status(
            self.session.warning
            or "Ready · Select a page with the arrow keys and Enter"
        )
        if self.refresh_on_start:
            self.action_refresh()

    def run_job(
        self,
        title: str,
        task: Callable[[Event], T],
        done: Callable[[T], object] | None = None,
    ) -> bool:
        if self.busy or self.reviewing or self.closing:
            self.notify("Finish or cancel the current task first.", severity="warning")
            return False
        for event in self.search_events.values():
            event.set()
        self.busy = True
        self.cancel_event = Event()
        self.status(title + "…")

        def worker() -> None:
            value: Any = None
            failure = ""
            try:
                if self.cancel_event.is_set():
                    raise AdapterError(
                        ErrorCode.CANCELLED, "Cancelled before starting work."
                    )
                value = task(self.cancel_event)
            except (AdapterError, StateError, ValueError) as error:
                failure = (
                    error.error.message
                    if isinstance(error, AdapterError)
                    else str(error)
                )
            except Exception:
                failure = "The task could not be completed. Inspect native state before retrying; partial changes may remain."
            self.session.sync_records()
            self.call_from_thread(self._finished, title, value, failure, done)

        self.run_worker(worker, thread=True, group="operations", exit_on_error=False)
        return True

    def _finished(
        self, title: str, value: Any, failure: str, done: Callable[[Any], object] | None
    ) -> None:
        self.busy = False
        self._drain_logs()
        self.status(failure or self.session.warning or title + " completed")
        if failure:
            self.notify(plain(failure), severity="error", timeout=10)
        elif done and not self.closing:
            done(value)
        if not self.closing:
            self._history()
            self._recovery()
        self._maybe_quit()

    def action_cancel(self) -> None:
        self.cancel_event.set()
        for event in self.search_events.values():
            event.set()
        self.status(
            "Cancellation requested; waiting for native work to stop. Partial changes may remain."
        )

    def action_request_quit(self) -> None:
        if self.closing:
            return
        self.closing = True
        if self.search_timer:
            self.search_timer.stop()
        self.action_cancel()
        self._maybe_quit()

    async def action_quit(self) -> None:
        self.action_request_quit()

    def _maybe_quit(self) -> None:
        if self.closing and not self.busy and not self.search_events:
            self.session.close()
            self.exit()

    def on_unmount(self) -> None:
        self.cancel_event.set()
        for event in self.search_events.values():
            event.set()
        self.session.close()

    def action_refresh(self) -> None:
        self.run_job("Refresh inventory", self.services.refresh, self._refreshed)

    def _refreshed(self, value: object) -> None:
        if not isinstance(value, DesktopSnapshot):
            return
        self.snapshot = value
        rows = [
            (
                str(i),
                (
                    r.native_id,
                    ", ".join(r.versions),
                    r.backend,
                    r.source_id
                    or ("PATH observation" if r.backend == "executable" else "unknown"),
                ),
            )
            for i, r in enumerate(value.installed)
        ]
        self._table("installed-table", rows)
        self.query_one("#inventory-status", Static).update(
            plain(
                f"{len(value.installed)} installed/observed tools; {len(value.inventory.snapshots)} native inventory sources.\n"
                "PATH observations are not proof of package ownership.\n"
                + "\n".join(
                    f"{backend}: {error.message}"
                    for backend, error in value.inventory.errors
                )
                + "\n".join(
                    f"\n{d.package_id}: executable present; {d.error.message}"
                    for d in value.inventory.detections
                    if d.present and d.error
                )
                + (
                    "\nNo native inventory could be read. PATH observations are shown when available."
                    if not value.inventory.snapshots
                    else ""
                )
            )
        )
        self.query_one("#backends", Static).update(
            plain(
                "\n".join(
                    f"{a.backend}: {'available' if a.available else a.reason or 'unavailable'}"
                    for a in value.backends
                )
                + "\n"
                + "\n".join(
                    f"{backend}: {error.message}"
                    for backend, error in value.inventory.errors
                )
            )
        )

    def action_search(self) -> None:
        self.navigate("Explore")
        self.query_one("#search", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            if self.search_timer:
                self.search_timer.stop()
            for cancel in self.search_events.values():
                cancel.set()
            self.search_generation += 1
            self.search_timer = self.set_timer(
                self.session.settings.search_delay_ms / 1000, self._search
            )

    def _search(self) -> None:
        if self.closing:
            return
        query = self.query_one("#search", Input).value
        category = self.query_one("#category", Select).value
        category = str(category) if category != "all" else None
        generation = self.search_generation
        initial = self.services.discovery.initial(
            query, category=category, enabled=False
        )
        self._render_search(initial)
        if self.busy or not self.session.settings.live_search or not query.strip():
            return
        self.search_generation += 1
        generation = self.search_generation
        cancel = Event()
        self.search_events[generation] = cancel
        self.query_one("#search-status", Static).update(
            "Searching available native sources…"
        )

        def worker() -> None:
            try:
                result = self.services.discovery.search(
                    query,
                    category=category,
                    cancel=cancel,
                    observe=lambda snapshot: self.call_from_thread(
                        self._search_partial, generation, snapshot
                    ),
                )
            except Exception:
                result = initial
            self.call_from_thread(self._search_finished, generation, result)

        self.run_worker(worker, thread=True, group="search", exit_on_error=False)

    def _search_partial(self, generation: int, result: SearchSnapshot) -> None:
        if generation == self.search_generation and not self.closing:
            self._render_search(result)

    def _search_finished(self, generation: int, result: SearchSnapshot) -> None:
        self.search_events.pop(generation, None)
        if generation == self.search_generation and not self.closing:
            self._render_search(result)
        self._maybe_quit()

    def _render_search(self, result: SearchSnapshot) -> None:
        rows = []
        self.search_rows = {}
        for match in result.curated:
            package = match.package
            key = "p-" + package.id
            self.search_rows[key] = package.id
            rows.append((key, (package.name, package.category, "curated", package.id)))
        for live_package in result.live:
            key = "l-" + hashlib.sha256(repr(live_package.key).encode()).hexdigest()
            self.search_rows[key] = live_package
            rows.append(
                (
                    key,
                    (
                        live_package.name,
                        "live",
                        live_package.backend,
                        live_package.native_id,
                    ),
                )
            )
        self._table("catalog", rows)
        statuses = ", ".join(
            f"{s.provider_id}: {s.state.value}" for s in result.providers
        )
        self.query_one("#search-status", Static).update(
            plain(f"{len(rows)} results · {statuses or 'curated catalog'}")
        )

    def _table(
        self, identity: str, rows: Iterable[tuple[str, Sequence[object]]]
    ) -> None:
        widget = self.query_one("#" + identity, DataTable)
        selected = self._selected(identity)
        widget.clear()
        for key, values in rows:
            widget.add_row(*(Text(plain(value)) for value in values), key=key)
        if selected is not None and any(key.value == selected for key in widget.rows):
            widget.move_cursor(row=widget.get_row_index(selected))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "catalog":
            if str(event.row_key.value) != self._selected("catalog"):
                return
            item = self.search_rows.get(str(event.row_key.value))
            if isinstance(item, str) and item == self.selection:
                return
            self.query_one("#package-version", Input).value = ""
            self.selection = item if isinstance(item, str) else None
            if isinstance(item, LivePackage):
                self.query_one("#details", Static).update(
                    plain(
                        f"{item.name}\n{item.summary}\nLive / {item.backend} / {item.source_id} / {item.native_id}\nBrowse-only; no reviewed installation mapping.\n"
                        + "\n".join(item.caveats)
                    )
                )
            elif self.selection:
                package = self.services.catalog.get(self.selection)
                if package:
                    self.query_one("#source", Select).set_options(
                        [
                            (
                                f"{s.support.os.value} / {s.backend} / {s.native_id}",
                                str(i),
                            )
                            for i, s in enumerate(package.install_specs)
                        ]
                    )
                    selected = next(
                        (
                            i
                            for i, s in enumerate(package.install_specs)
                            if s.support.supports(self.services.host)
                        ),
                        0,
                    )
                    self.query_one("#source", Select).value = str(selected)
                    self._details()

    def _details(self) -> None:
        package = self.services.catalog.get(self.selection) if self.selection else None
        if package:
            sources = "\n".join(
                f"{s.support.os.value}: {s.backend} / {s.source.id} / {s.native_id}"
                for s in package.install_specs
            )
            selected = self.query_one("#source", Select).value
            spec = (
                package.install_specs[int(selected)]
                if isinstance(selected, str) and selected.isdigit()
                else None
            )
            details = [
                package.name,
                package.summary,
                f"Homepage: {package.homepage}",
                f"Documentation: {package.documentation or 'not supplied'}",
                f"Vendor: {package.vendor or 'unknown'} · License: {package.license or 'unknown'}",
                f"Dependencies: {', '.join(d.package_id for d in package.dependencies) or 'none'}",
                sources,
            ]
            if spec:
                details.extend(
                    (
                        f"Selected source: {spec.source.id} ({spec.source.url})",
                        f"Compatible mapping: {'yes' if spec.support.supports(self.services.host) else 'unsupported on this platform'}",
                        f"Privileges: {spec.privilege.value} · Estimated bytes: {spec.estimated_size_bytes or 'unknown'}",
                        f"Restart required: {'yes' if spec.restart_required else 'not declared'}",
                        *spec.notes,
                    )
                )
            self.query_one("#details", Static).update(plain("\n".join(details)))

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "category":
            for cancel in self.search_events.values():
                cancel.set()
            self.search_generation += 1
            self._search()
        elif event.select.id == "source":
            self._details()
        elif (
            event.select.id == "preset"
            and isinstance(event.value, str)
            and event.value.isdigit()
        ):
            self._load_manifest(self.presets[int(event.value)])

    def _request(self, action: OperationAction) -> PackageRequest:
        if not self.selection:
            raise ValueError("Select a curated package first.")
        package = self.services.catalog.get(self.selection)
        source = self.query_one("#source", Select).value
        if package is None or not isinstance(source, str) or not source.isdigit():
            raise ValueError("Select a reviewed package source.")
        spec = package.install_specs[int(source)]
        version = self.query_one("#package-version", Input).value.strip()
        return PackageRequest(
            package.id,
            action,
            spec.backend,
            spec.source.id,
            PackageVersion(version) if version else None,
        )

    def _cart(self) -> None:
        self._table(
            "cart",
            [
                (
                    key,
                    (
                        r.package_id,
                        r.action.value,
                        r.backend or "automatic",
                        r.source_id or "reviewed mapping",
                    ),
                )
                for key, r in self.cart.items()
            ],
        )

    def preview(self, requests: tuple[PackageRequest, ...]) -> None:
        if not requests:
            self.notify("Add packages to the queue first.", severity="warning")
            return
        self.run_job(
            "Resolve combined plan",
            lambda cancel: self.services.planner.preview(requests, cancel=cancel),
            self._package_plan,
        )

    def _show_review(
        self,
        title: str,
        body: str,
        apply: Callable[[Decision], object],
        *,
        ready: bool = True,
        elevation: bool = False,
    ) -> None:
        self.reviewing = True

        def decided(decision: Decision | None) -> None:
            self.reviewing = False
            if decision and not self.closing:
                apply(decision)

        self.push_screen(Review(title, body, ready=ready, elevation=elevation), decided)

    def _package_plan(self, value: object) -> None:
        if not isinstance(value, ReviewedPlan):
            return
        text, elevation = package_review(value)
        self._show_review(
            "Package plan",
            text,
            lambda decision: self._execute(value, decision),
            ready=value.plan.ready,
            elevation=elevation,
        )

    def _manifest_plan(self, value: ManifestPreview) -> None:
        text, elevation = package_review(value.reviewed)
        if value.unsupported:
            text += "\n\nSeparate actions or unsupported choices:\n" + "\n".join(
                value.unsupported
            )
        self._show_review(
            "Collection package plan",
            text,
            lambda decision: self._execute(value.reviewed, decision),
            ready=value.reviewed.plan.ready,
            elevation=elevation,
        )

    def _execute(self, plan: ReviewedPlan, decision: Decision) -> None:
        self.navigate("Queue")
        self.run_job(
            "Execute approved plan",
            lambda cancel: self.session.execute(
                plan,
                elevation=decision.elevation,
                cancel=cancel,
                observe=lambda event: self.call_from_thread(self._progress, event),
                logs=self._output,
            ),
            self._executed,
        )

    def _progress(self, event: ProgressEvent) -> None:
        self.query_one("#operation-status", Static).update(
            plain(f"{event.package_id}: {event.state.value} · {event.message}")
        )

    def _output(self, operation_id: str, output: OutputSnapshot) -> None:
        with self._log_lock:
            self._logs[(operation_id, output.stream)] = plain(output.text[-65536:])
            self._logs.move_to_end((operation_id, output.stream))
            while len(self._logs) > 32:
                self._logs.popitem(last=False)
            self._logs_dirty = True

    def _drain_logs(self) -> None:
        with self._log_lock:
            if not self._logs_dirty:
                return
            text = "\n\n".join(
                f"{key[0]} / {key[1]}\n{value}" for key, value in self._logs.items()
            )[-65536:]
            self._logs_dirty = False
        self.query_one("#logs", TextArea).load_text(text)

    def _executed(self, value: object) -> None:
        if isinstance(value, ExecutionReport):
            self.query_one("#operation-status", Static).update(
                plain(
                    "\n".join(
                        f"{op.item.package_id}: {op.state.value} · {op.result.message if op.result else ''}"
                        for op in value.operations
                    )
                )
            )
            self.status(
                "Plan result: "
                + value.outcome.value
                + (
                    " · " + "; ".join(value.persistence_errors)
                    if value.persistence_errors
                    else ""
                )
            )
        self.cart.clear()
        self._cart()
        if not self.closing:
            self.action_refresh()

    def _history(self) -> None:
        self._table(
            "history-table",
            [
                (
                    str(i),
                    (
                        r.package_id,
                        r.action,
                        r.state,
                        r.backend,
                        str(r.finished_at),
                    ),
                )
                for i, r in enumerate(self.session.history)
            ],
        )

    def _recovery(self) -> None:
        try:
            records = self.session.records()
        except Exception:
            records = ()
        self.recovery_rows = {r.operation_id: r for r in records if r.needs_recovery}
        self._table(
            "recovery-table",
            [
                (
                    r.operation_id,
                    (
                        r.package_id,
                        r.state.value,
                        r.verification,
                        "inspection required",
                    ),
                )
                for r in self.recovery_rows.values()
            ],
        )

    def _selected(self, identity: str) -> str | None:
        table = self.query_one("#" + identity, DataTable)
        if not table.row_count:
            return None
        return str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)

    def _load_manifest(self, manifest: Manifest) -> None:
        self.manifest = manifest
        self.query_one("#collection-name", Input).value = manifest.name
        self.query_one("#collection-packages", Input).value = ", ".join(
            p.package_id for p in manifest.packages
        )
        text = f"{manifest.name}\n{manifest.description}\n" + "\n".join(
            f"Runtime: {r.manager} / {r.runtime}@{r.version}" for r in manifest.runtimes
        )
        text += "\n" + "\n".join(
            f"Project configuration: {c.kind}@{c.version}"
            for c in manifest.configurations
        )
        text += "\n" + "\n".join(
            f"Project environment: {e.name}={e.relative_path}"
            for e in manifest.environment
        )
        self.query_one("#manifest-details", TextArea).load_text(plain(text))
        if manifest.runtimes and manifest.runtimes[0].manager in {
            "mise",
            "uv",
            "rustup",
        }:
            request = manifest.runtimes[0]
            self.query_one("#runtime-manager", Select).value = request.manager
            self.query_one("#runtime-name", Input).value = request.runtime
            self.query_one("#runtime-version", Input).value = request.version
        if manifest.configurations:
            choice = manifest.configurations[0]
            self.query_one("#config-kind", Select).value = choice.kind
            self.query_one("#config-version", Input).value = choice.version
        if manifest.environment:
            setting = manifest.environment[0]
            self.query_one("#env-name", Select).value = setting.name
            self.query_one("#env-path", Input).value = setting.relative_path

    def _manifest(self) -> Manifest:
        ids = tuple(
            p.strip()
            for p in self.query_one("#collection-packages", Input).value.split(",")
            if p.strip()
        )
        old = {p.package_id: p for p in self.manifest.packages}
        return replace(
            self.manifest,
            name=self.query_one("#collection-name", Input).value.strip(),
            packages=tuple(old.get(p) or ManifestPackage(p) for p in ids),
        )

    def _runtime_request(self) -> RuntimeRequest:
        return RuntimeRequest(
            str(self.query_one("#runtime-manager", Select).value),
            self.query_one("#runtime-name", Input).value.strip(),
            self.query_one("#runtime-version", Input).value.strip(),
        )

    def _runtime_plan(self, plan: object) -> None:
        if isinstance(plan, RuntimePlan):
            self._show_review(
                "Runtime installation",
                runtime_review(plan),
                lambda decision: self.run_job(
                    "Install approved runtime",
                    lambda cancel: self.session.runtime_execute(
                        plan,
                        cancel,
                        observe=lambda snapshot: self._output("runtime", snapshot),
                    ),
                    self._runtime_result,
                ),
            )

    def _runtime_result(self, result: RuntimeResult) -> None:
        message = f"{result.state.value}: {result.message}"
        if result.persistence_errors:
            message += " · " + "; ".join(result.persistence_errors)
        self.status(message)
        self.query_one("#operation-status", Static).update(plain(message))

    def _config_plan(self, plan: object) -> None:
        if isinstance(plan, ConfigPlan):
            self._show_review(
                "Project file change",
                config_review(plan),
                lambda decision: self.run_job(
                    "Apply approved project file",
                    lambda cancel: self.session.files.apply_config(
                        plan, confirmed=True, cancel=cancel
                    ),
                ),
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if self.busy or self.reviewing:
            if event.button.id == "cancel":
                self.action_cancel()
            else:
                self.notify(
                    "Finish or cancel the current task first.", severity="warning"
                )
            return
        try:
            self._button(event.button.id or "")
        except (ValueError, IndexError) as error:
            self.notify(plain(error), severity="error")

    def _button(self, action: str) -> None:
        self.actions.handle(action)

    def _updates(self, value: object) -> None:
        if isinstance(value, UpdateReport):
            self.offers = value
            self._table(
                "updates-table",
                [
                    (
                        str(i),
                        (
                            r.package_id,
                            ", ".join(r.installed),
                            r.candidate,
                            r.spec.source.id,
                        ),
                    )
                    for i, r in enumerate(value.candidates)
                ],
            )
            self.query_one("#update-errors", Static).update(
                plain("\n".join(value.errors) or "No reviewed upgrades offered.")
            )

    def _diagnostics(self, value: Any) -> None:
        self.diagnostics = value
        self.query_one("#support-details", TextArea).load_text(diagnostic_json(value))
