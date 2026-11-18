"""Route terminal user intent to reviewed background application workflows."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

from textual.widgets import Checkbox, Input, Select, TextArea

from app import __version__
from models.environment import (
    ConfigPlan,
    EnvironmentChoice,
    ProjectConfigChoice,
)
from models.planning import OperationAction
from persistence.store import DesktopSettings
from services.diagnostics import (
    collect_diagnostics,
    diagnostic_json,
    export_diagnostics,
)
from services.environments import preview_manifest
from services.records import PackageRequest
from services.updates import UpdateService

from .review import plain

if TYPE_CHECKING:
    from .app import DevSetupApp


class Actions:
    def __init__(self, app: DevSetupApp) -> None:
        self.app = app

    def handle(self, action: str) -> None:
        if action in {"refresh", "refresh-installed"}:
            self.app.action_refresh()
        elif action == "cancel":
            self.app.action_cancel()
        elif action.startswith("add-") or action == "verify":
            request = self.app._request(OperationAction(action.removeprefix("add-")))
            if action == "verify":
                self.app.preview((request,))
            else:
                self.app.cart[request.package_id] = request
                self.app._cart()
                self.app.status(f"Added {request.package_id} to the combined plan")
        elif action in {"preview", "queue-review"}:
            self.app.preview(tuple(self.app.cart.values()))
        elif action == "queue-remove":
            selected = self.app._selected("cart")
            if selected:
                self.app.cart.pop(selected, None)
                self.app._cart()
        elif action == "inspect-installed":
            key = self.app._selected("installed-table")
            if key is not None and self.app.snapshot:
                row = self.app.snapshot.installed[int(key)]
                if row.package_id:
                    self.app.navigate("Explore")
                    self.app.query_one("#search", Input).value = row.package_id
                else:
                    self.app.notify(
                        "This installed package has no unique reviewed catalog mapping."
                    )
        elif action == "tool-updates":
            if self.app.snapshot is None:
                raise ValueError("Refresh installed inventory first.")
            snapshot = self.app.snapshot
            self.app.run_job(
                "Check tool updates",
                lambda cancel: self.app.services.updates(snapshot, cancel),
                self.app._updates,
            )
        elif action == "review-updates":
            self.app.preview(
                tuple(
                    PackageRequest(
                        r.package_id,
                        OperationAction.UPGRADE,
                        r.spec.backend,
                        r.spec.source.id,
                    )
                    for r in self.app.offers.candidates
                )
                if self.app.offers
                else ()
            )
        elif action in {"recovery-inspect", "recovery-ack"}:
            key = self.app._selected("recovery-table")
            record = self.app.recovery_rows.get(key) if key else None
            if record is None:
                raise ValueError("Select an interrupted operation first.")
            if action == "recovery-inspect":
                self.app.run_job(
                    "Inspect interrupted work",
                    lambda cancel: self.app.session.recovery.inspect(record, cancel),
                    lambda result: self.app.status(
                        f"Inspection: {result.verification} · original state: {result.state.value}"
                    ),
                )
            else:
                self.app._show_review(
                    "Acknowledge inspected outcome",
                    f"{record.package_id}\nOriginal outcome: {record.state.value}\nInspection: {record.verification}\nAcknowledgement does not retry or roll back the operation.",
                    lambda decision: self.app.run_job(
                        "Acknowledge recovery",
                        lambda cancel: self.app.session.recovery.reconcile(
                            record, confirmed=True
                        ),
                    ),
                    ready=record.inspected_at is not None
                    and record.verification not in {"unknown", "unavailable"},
                )
        elif action == "manifest-import":
            path = Path(self.app.query_one("#manifest-path", Input).value)
            self.app.run_job(
                "Import manifest",
                lambda cancel: self.app.session.files.import_manifest(path, cancel),
                self.app._load_manifest,
            )
        elif action == "manifest-export":
            manifest = self.app._manifest()
            path = Path(self.app.query_one("#manifest-path", Input).value)
            self.app._show_review(
                "Export collection",
                f"Create a new manifest at {path}\n{manifest.name}",
                lambda decision: self.app.run_job(
                    "Export manifest",
                    lambda cancel: self.app.session.files.export_manifest(
                        manifest, path, cancel
                    ),
                ),
            )
        elif action == "manifest-preview":
            manifest = self.app._manifest()
            self.app.run_job(
                "Preview portable packages",
                lambda cancel: preview_manifest(
                    manifest, self.app.services.planner, cancel
                ),
                self.app._manifest_plan,
            )
        elif action == "runtime-add":
            runtime_request = self.app._runtime_request()
            current = tuple(
                r
                for r in self.app.manifest.runtimes
                if (r.manager, r.runtime)
                != (runtime_request.manager, runtime_request.runtime)
            )
            self.app._load_manifest(
                replace(self.app._manifest(), runtimes=(*current, runtime_request))
            )
            self.app.status("Runtime choice added to the current collection")
        elif action == "runtime-preview":
            runtime_request = self.app._runtime_request()
            self.app.run_job(
                "Review runtime",
                lambda cancel: self.app.session.runtimes.preview(
                    runtime_request, cancel
                ),
                self.app._runtime_plan,
            )
        elif action in {"config-preview", "env-preview"}:
            project = Path(self.app.query_one("#project-path", Input).value)
            if action == "config-preview":
                kind, version = (
                    str(self.app.query_one("#config-kind", Select).value),
                    self.app.query_one("#config-version", Input).value,
                )

                def task(cancel: Event) -> ConfigPlan:
                    return self.app.session.config.preview(project, kind, version)
            else:
                name, relative_path = (
                    str(self.app.query_one("#env-name", Select).value),
                    self.app.query_one("#env-path", Input).value,
                )

                def task(cancel: Event) -> ConfigPlan:
                    return self.app.session.config.preview_environment(
                        project, name, relative_path
                    )

            self.app.run_job("Preview project change", task, self.app._config_plan)
        elif action == "config-add":
            choice = ProjectConfigChoice(
                str(self.app.query_one("#config-kind", Select).value),
                self.app.query_one("#config-version", Input).value.strip(),
            )
            retained = tuple(
                c for c in self.app.manifest.configurations if c.kind != choice.kind
            )
            self.app._load_manifest(
                replace(self.app._manifest(), configurations=(*retained, choice))
            )
        elif action == "env-add":
            env_choice = EnvironmentChoice(
                str(self.app.query_one("#env-name", Select).value),
                self.app.query_one("#env-path", Input).value.strip(),
            )
            retained_env = tuple(
                e for e in self.app.manifest.environment if e.name != env_choice.name
            )
            self.app._load_manifest(
                replace(self.app._manifest(), environment=(*retained_env, env_choice))
            )
        elif action == "history-retry":
            key = self.app._selected("history-table")
            if key is None:
                raise ValueError("Select an audit item first.")
            audit_item = self.app.session.history[int(key)]
            self.app.preview(
                (
                    PackageRequest(
                        audit_item.package_id,
                        OperationAction(audit_item.action),
                        audit_item.backend,
                        audit_item.source_id,
                    ),
                )
            )
        elif action == "settings-save":
            settings = DesktopSettings(
                str(self.app.query_one("#theme-choice", Select).value),
                int(self.app.query_one("#search-delay", Input).value),
                self.app.query_one("#live-search", Checkbox).value,
            )
            self.app.run_job(
                "Save settings",
                lambda cancel: self.app.session.save_settings(settings),
                lambda value: setattr(
                    self.app,
                    "theme",
                    "textual-dark" if settings.theme == "dark" else "textual-light",
                ),
            )
        elif action == "history-clear":
            self.app._show_review(
                "Clear audit history",
                "Remove saved audit history. Unresolved lifecycle/recovery records are preserved.",
                lambda decision: self.app.run_job(
                    "Clear audit history",
                    lambda cancel: self.app.session.clear_history(),
                ),
            )
        elif action == "diagnostics-preview":
            self.app.run_job(
                "Preview diagnostics",
                lambda cancel: collect_diagnostics(
                    __version__,
                    self.app.services.host,
                    self.app.snapshot.backends if self.app.snapshot else (),
                ),
                self.app._diagnostics,
            )
        elif action == "diagnostics-export":
            if self.app.diagnostics is None:
                raise ValueError("Preview diagnostics before exporting them.")
            report, path = (
                self.app.diagnostics,
                Path(self.app.query_one("#diagnostics-path", Input).value),
            )
            self.app._show_review(
                "Export diagnostics",
                f"Create {path}\n" + diagnostic_json(report),
                lambda decision: self.app.run_job(
                    "Export diagnostics",
                    lambda cancel: export_diagnostics(report, path),
                ),
            )
        elif action == "app-updates":
            self.app.run_job(
                "Check application updates",
                lambda cancel: UpdateService().check(
                    __version__, self.app.services.host, cancel
                ),
                lambda value: self.app.query_one(
                    "#support-details", TextArea
                ).load_text(plain(value)),
            )
