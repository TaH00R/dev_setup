"""UI-independent persistent application session used by the terminal front end."""

from collections.abc import Callable
from pathlib import Path
from threading import Event

from models.environment import RuntimeConsent, RuntimePlan, RuntimeResult
from models.lifecycle import LifecycleRecord
from models.operations import ProgressEvent
from persistence.store import DesktopSettings, StateStore
from runtimes.service import RuntimeService
from services.desktop import DesktopServices
from services.environments import ProjectConfigService
from services.lifecycle import RecoveryService, UnavailableLifecycleStore
from services.records import AuditRecord, ExecutionReport, PlanConsent, ReviewedPlan
from system.commands import OutputSnapshot
from system.runner import OutputObserver

from .environments import EnvironmentWorkflow


class Session:
    def __init__(self, services: DesktopServices, path: Path | None = None) -> None:
        self.services, self.path = services, path
        self.store: StateStore | None = None
        self.settings = DesktopSettings()
        self.warning = ""
        self.history: list[AuditRecord] = []
        self.lifecycle: tuple[LifecycleRecord, ...] = ()
        self.runtimes = RuntimeService(services.host, services.operations.runner)
        self.config = ProjectConfigService()
        self.files = EnvironmentWorkflow(services.planner, self.config)

    def open(self) -> None:
        if self.path is not None:
            try:
                self.store = StateStore(self.path)
                self.settings = self.store.load_settings()
                self.history = list(self.store.load_history())
                self.store.interrupt_active()
            except Exception:
                if self.store:
                    self.store.close()
                self.store = None
                self.warning = "Saved state is unavailable. Browsing remains available; persistent mutations are blocked."
        if self.path is not None:
            lifecycle = self.store or UnavailableLifecycleStore()
            self.services.operations.lifecycle_store = lifecycle
            self.runtimes.lifecycle_store = lifecycle

    @property
    def recovery(self) -> RecoveryService:
        return RecoveryService(
            self.store or self.services.operations.effective_store,
            self.services.host,
            self.services.inspectors,
            self.runtimes,
        )

    def records(self) -> tuple[LifecycleRecord, ...]:
        return self.lifecycle

    def sync_records(self) -> None:
        try:
            self.lifecycle = (
                self.store or self.services.operations.effective_store
            ).load_lifecycle()
        except Exception:
            self.warning = "Recovery state could not be read. Inspect storage before starting native changes."

    def execute(
        self,
        plan: ReviewedPlan,
        *,
        elevation: bool,
        cancel: Event,
        observe: Callable[[ProgressEvent], None] | None = None,
        logs: Callable[[str, OutputSnapshot], None] | None = None,
    ) -> ExecutionReport:
        report = self.services.operations.execute(
            plan,
            consent=PlanConsent(plan, True, elevation),
            cancel=cancel,
            observe=observe,
            logs=logs,
        )
        self.history = [*report.audit, *self.history][:200]
        if self.store:
            try:
                self.store.append_audit(report.audit)
            except Exception:
                self.warning = "The result is available in this session, but audit history could not be saved."
        return report

    def runtime_execute(
        self, plan: RuntimePlan, cancel: Event, observe: OutputObserver | None = None
    ) -> RuntimeResult:
        return self.runtimes.execute(plan, RuntimeConsent(plan, True), cancel, observe)

    def save_settings(self, settings: DesktopSettings) -> None:
        if self.store:
            self.store.save_settings(settings)
        elif self.path is not None:
            raise ValueError("Saved settings are unavailable.")
        self.settings = settings

    def clear_history(self) -> None:
        if self.store:
            self.store.clear_history()
        self.history.clear()

    def close(self) -> None:
        if self.store:
            self.store.close()
