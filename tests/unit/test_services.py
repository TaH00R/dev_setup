"""Service acceptance tests: no native process or package change is allowed."""

import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from models.errors import ErrorCode, OperationError
from models.inventory import Detection, InstalledPackage, Inventory, NativeReview
from models.operations import OperationState as State
from models.package import Dependency, PackageVersion
from models.planning import OperationAction as Action
from models.planning import utc_now
from models.platform import Architecture, OperatingSystem, Platform
from package_managers.base import AdapterError
from package_managers.homebrew import Homebrew
from services.catalog.loader import load_catalog
from services.catalog.service import CatalogService
from services.inventory import InventoryService
from services.operations import OperationService
from services.planning import Planner
from services.records import (
    PackageRequest,
    PlanConsent,
)
from system.commands import CommandResult, OutputSnapshot
from system.runner import CommandRunner


class FakeInspector:
    def __init__(self, adapter):
        self.adapter = adapter
        self.installed = {}
        self.versions = {}
        self.fingerprint = "reviewed"
        self.error = None
        self.extra_changes = ()

    def inventory(self, cancel=None):
        if self.error:
            raise AdapterError(self.error, "Native inventory unavailable.")
        return Inventory(self.adapter.id, tuple(self.installed.values()))

    def review(self, spec, action, cancel=None):
        return NativeReview(
            None
            if action == Action.UNINSTALL
            else self.versions.get(spec.native_id, "2.0"),
            self.fingerprint,
            (f"Native {action.value} {spec.native_id}", *self.extra_changes),
        )


class ServiceTests(unittest.TestCase):
    def test_dependency_exact_version_merges_independently_of_request_order(self):
        alpha, beta, *rest = self.packages
        self.install("beta", "2.0")
        packages = (
            replace(alpha, dependencies=(Dependency("beta", PackageVersion("2.0")),)),
            beta,
            *rest,
        )
        for requests in (
            (PackageRequest("beta"), PackageRequest("alpha")),
            (PackageRequest("alpha"), PackageRequest("beta")),
        ):
            plan = self.preview(*requests, packages=packages)
            self.assertTrue(plan.plan.ready, plan.plan.issues)
            self.assertEqual(plan.plan.items[0].spec.version.value, "2.0")

    def test_executable_observation_warns_without_claiming_native_identity(self):
        plan = self.planner().preview(
            (PackageRequest("alpha"),),
            observations=(Detection("alpha", "git", True, "git version 2.0"),),
        )
        self.assertTrue(plan.plan.items[0].requires_consent)
        self.assertIn("already on PATH", " ".join(plan.plan.items[0].changes))
        self.assertIn("unverified", " ".join(plan.plan.items[0].changes))
        self.assertNotIn("git version 2.0", " ".join(plan.plan.items[0].changes))

    def test_candidate_change_is_rejected_even_if_metadata_fingerprint_matches(self):
        plan = self.preview()
        self.inspector.versions["alpha"] = "3.0"
        self.assertEqual(self.execute(plan).operations[0].state, State.FAILED)
        self.runner.run.assert_not_called()

    def test_native_effects_may_shrink_after_prior_dependency_installation(self):
        self.inspector.extra_changes = ("Native dependency install",)
        plan = self.preview()
        self.inspector.extra_changes = ()
        self.assertEqual(self.execute(plan).operations[0].state, State.SUCCEEDED)

    def test_verification_detects_wrong_native_version_after_zero_exit(self):
        plan = self.preview()
        real = self.runner.run.side_effect

        def run(command, **kwargs):
            result = real(command, **kwargs)
            self.install("alpha", "unexpected")
            return result

        self.runner.run.side_effect = run
        report = self.execute(plan)
        self.assertEqual(report.operations[0].state, State.FAILED)
        self.assertEqual(report.operations[0].result.exit_code, 0)

    def test_inventory_rejects_alias_collisions_instead_of_guessing(self):
        with self.assertRaises(ValueError):
            Inventory(
                "apt",
                (
                    InstalledPackage("git", ("1",), aliases=("git:amd64",)),
                    InstalledPackage("git:amd64", ("2",)),
                ),
            )

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.host = Platform(OperatingSystem.MACOS, Architecture.ARM64)
        self.adapter = Homebrew(self.host, find=lambda name: str(self.root / name))
        template = next(p for p in load_catalog() if p.id == "git")
        spec = next(s for s in template.install_specs if s.backend == "homebrew")
        self.packages = tuple(
            replace(
                template,
                id=name,
                name=name.title(),
                dependencies=(),
                conflicts=(),
                install_specs=(replace(spec, native_id=name),),
            )
            for name in ("alpha", "beta", "gamma", "other")
        )
        self.inspector = FakeInspector(self.adapter)
        self.runner = mock.create_autospec(CommandRunner, instance=True)
        self.runner.run.side_effect = self.run_command
        self.mutate = True
        self.exit_code = 0
        self.command_error = None
        self.logs = []

    def run_command(self, command, *, approval=None, cancel=None, observe=None):
        self.assertTrue(command.mutates)
        self.assertIsNotNone(approval)
        self.assertTrue(approval.permits(command))
        if observe:
            observe(OutputSnapshot("stdout", "safe native output"))
        name = command.argv[-1]
        if self.mutate and not self.exit_code and self.command_error is None:
            if command.argv[1] == "uninstall":
                self.inspector.installed.pop(name, None)
            else:
                self.install(name, self.inspector.versions.get(name, "2.0"))
        now = utc_now()
        return CommandResult(
            command.preview(),
            now,
            now,
            self.exit_code,
            "safe native output",
            "",
            False,
            self.command_error,
        )

    def install(self, name, version="1.0", source="homebrew-core"):
        self.inspector.installed[name] = InstalledPackage(name, (version,), source)

    def planner(self, packages=None, inspectors=None):
        return Planner(
            CatalogService(packages or self.packages),
            self.host,
            (self.inspector,) if inspectors is None else inspectors,
        )

    def preview(self, *requests, packages=None):
        return self.planner(packages).preview(
            tuple(requests) or (PackageRequest("alpha"),)
        )

    def execute(self, reviewed, **kwargs):
        if "consent" not in kwargs:
            kwargs["consent"] = PlanConsent(reviewed, True, True)
        return OperationService((self.inspector,), self.runner).execute(
            reviewed, **kwargs
        )

    def test_preview_orders_shared_dependencies_and_never_executes(self):
        alpha, beta, gamma, other = self.packages
        packages = (
            replace(alpha, dependencies=(Dependency("beta"), Dependency("gamma"))),
            replace(beta, dependencies=(Dependency("gamma"),)),
            gamma,
            other,
        )
        plan = self.preview(packages=packages)
        self.assertTrue(plan.plan.ready)
        self.assertEqual(
            [i.package_id for i in plan.plan.items], ["gamma", "beta", "alpha"]
        )
        self.runner.run.assert_not_called()
        self.assertTrue(all(i.commands and i.changes for i in plan.plan.items))
        self.assertEqual(plan.plan.items[-1].spec.source.id, "homebrew-core")

    def test_cycles_produce_non_executable_plan(self):
        alpha, beta, *rest = self.packages
        plan = self.preview(
            packages=(
                replace(alpha, dependencies=(Dependency("beta"),)),
                replace(beta, dependencies=(Dependency("alpha"),)),
                *rest,
            )
        )
        self.assertEqual(plan.plan.issues[0].code, ErrorCode.DEPENDENCY_CYCLE)
        self.assertFalse(plan.plan.items)

    def test_requested_and_installed_conflicts_are_symmetric(self):
        alpha, beta, *rest = self.packages
        packages = (alpha, replace(beta, conflicts=("alpha",)), *rest)
        for installed in (False, True):
            with self.subTest(installed=installed):
                if installed:
                    self.install("beta")
                    plan = self.preview(packages=packages)
                else:
                    plan = self.preview(
                        PackageRequest("alpha"),
                        PackageRequest("beta"),
                        packages=packages,
                    )
                self.assertEqual(
                    plan.plan.issues[0].code, ErrorCode.DEPENDENCY_CONFLICT
                )

    def test_reverse_dependency_blocks_removal(self):
        alpha, beta, *rest = self.packages
        self.install("alpha")
        self.install("beta")
        plan = self.preview(
            PackageRequest("beta", Action.UNINSTALL),
            packages=(replace(alpha, dependencies=(Dependency("beta"),)), beta, *rest),
        )
        self.assertEqual(plan.plan.issues[0].code, ErrorCode.DEPENDENCY_CONFLICT)

    def test_dependency_version_conflict_and_removed_dependency(self):
        alpha, beta, *rest = self.packages
        packages = (
            replace(alpha, dependencies=(Dependency("beta", PackageVersion("2.0")),)),
            beta,
            *rest,
        )
        for request in (
            PackageRequest("beta", version=PackageVersion("1.0")),
            PackageRequest("beta", Action.UNINSTALL),
        ):
            plan = self.preview(PackageRequest("alpha"), request, packages=packages)
            self.assertEqual(plan.plan.issues[0].code, ErrorCode.DEPENDENCY_CONFLICT)

    def test_no_mapping_ambiguous_source_and_missing_inspector(self):
        plan = self.preview(PackageRequest("alpha", backend="apt"))
        self.assertEqual(plan.plan.issues[0].code, ErrorCode.UNSUPPORTED_PLATFORM)
        alpha, *rest = self.packages
        spec = alpha.install_specs[0]
        alternate = replace(spec, source=replace(spec.source, id="alternative"))
        plan = self.preview(
            packages=(replace(alpha, install_specs=(spec, alternate)), *rest)
        )
        self.assertFalse(plan.plan.ready)
        plan = self.planner(inspectors=()).preview((PackageRequest("alpha"),))
        self.assertEqual(plan.plan.issues[0].code, ErrorCode.UNSUPPORTED_OPERATION)

    def test_duplicate_requests_missing_package_and_backend(self):
        self.assertFalse(self.preview(PackageRequest("unknown")).plan.ready)
        self.assertFalse(
            self.preview(PackageRequest("alpha"), PackageRequest("alpha")).plan.ready
        )
        self.adapter.find = lambda name: None
        self.assertEqual(self.preview().plan.issues[0].code, ErrorCode.MISSING_BACKEND)

    def test_inventory_failure_is_not_absence(self):
        for code in (
            ErrorCode.NETWORK,
            ErrorCode.TIMEOUT,
            ErrorCode.COMMAND_FAILED,
            ErrorCode.CANCELLED,
        ):
            self.inspector.error = code
            plan = self.preview()
            self.assertFalse(plan.plan.ready)
            self.assertEqual(plan.plan.issues[0].code, code)
        self.runner.run.assert_not_called()

    def test_idempotent_install_and_absent_removal_skip(self):
        self.install("alpha")
        for request in (
            PackageRequest("alpha"),
            PackageRequest("beta", Action.UNINSTALL),
        ):
            plan = self.preview(request)
            self.assertFalse(plan.plan.requires_consent)
            report = self.execute(plan, consent=None)
            self.assertEqual(report.operations[0].state, State.SKIPPED)
        self.runner.run.assert_not_called()

    def test_installed_version_mismatch_and_wrong_source(self):
        self.install("alpha", "1.0")
        plan = self.preview(PackageRequest("alpha", version=PackageVersion("2.0")))
        self.assertEqual(plan.plan.issues[0].code, ErrorCode.VERSION_MISMATCH)
        self.install("alpha", source="different-source")
        self.assertEqual(
            self.preview().plan.issues[0].code, ErrorCode.DEPENDENCY_CONFLICT
        )

    def test_explicit_version_unsupported_is_not_silently_dropped(self):
        plan = self.preview(PackageRequest("alpha", version=PackageVersion("2.0")))
        self.assertEqual(plan.plan.issues[0].code, ErrorCode.UNSUPPORTED_OPERATION)

    def test_success_streams_states_logs_and_output_free_audit(self):
        plan = self.preview()
        events, logs = [], []
        report = self.execute(
            plan,
            observe=events.append,
            logs=lambda op, output: logs.append((op, output)),
        )
        self.assertEqual(
            [e.state for e in events],
            [
                State.QUEUED,
                State.RESOLVING,
                State.AWAITING_CONSENT,
                State.INSTALLING,
                State.VERIFYING,
                State.SUCCEEDED,
            ],
        )
        operation = report.operations[0]
        self.assertEqual(operation.result.exit_code, 0)
        self.assertEqual(operation.result.stdout, "safe native output")
        self.assertIsNotNone(operation.consented_at)
        self.assertEqual(logs[0][0], operation.id)
        self.assertNotIn("safe native output", repr(report))
        self.assertFalse(hasattr(report.audit[0], "stdout"))
        self.assertIsNotNone(report.audit[0].approved_at)

    def test_no_rejected_or_different_plan_consent_can_execute(self):
        plan = self.preview()
        for consent in (
            None,
            PlanConsent(plan, False),
            PlanConsent(self.preview(), True),
        ):
            with self.subTest(consent=consent):
                report = self.execute(plan, consent=consent)
                self.assertEqual(report.operations[0].state, State.CANCELLED)
        self.runner.run.assert_not_called()

    def test_elevation_requires_separate_explicit_consent(self):
        from models.specs import PrivilegeRequirement

        alpha, *rest = self.packages
        alpha = replace(
            alpha,
            install_specs=(
                replace(
                    alpha.install_specs[0], privilege=PrivilegeRequirement.REQUIRED
                ),
            ),
        )
        plan = self.preview(packages=(alpha, *rest))
        report = self.execute(plan, consent=PlanConsent(plan, True, False))
        self.assertEqual(
            report.operations[0].result.error.code, ErrorCode.PERMISSION_DENIED
        )
        self.runner.run.assert_not_called()

    def test_stale_metadata_commands_inventory_and_native_effects_need_new_consent(
        self,
    ):
        for change in ("metadata", "command", "inventory", "effects"):
            with self.subTest(change=change):
                self.setUp()
                plan = self.preview()
                if change == "metadata":
                    self.inspector.fingerprint = "changed"
                elif change == "command":
                    self.adapter.find = lambda name: str(self.root / "other" / name)
                elif change == "inventory":
                    self.install("alpha", "0.9")
                else:
                    self.inspector.extra_changes = ("Unreviewed native removal",)
                report = self.execute(plan)
                self.assertEqual(report.operations[0].state, State.FAILED)
                self.assertEqual(
                    report.operations[0].result.error.code,
                    ErrorCode.VERIFICATION_FAILED,
                )
                self.runner.run.assert_not_called()

    def test_zero_exit_requires_fresh_native_verification(self):
        self.mutate = False
        report = self.execute(self.preview())
        self.assertEqual(report.operations[0].state, State.FAILED)
        self.assertEqual(
            report.operations[0].result.error.code, ErrorCode.VERIFICATION_FAILED
        )
        self.assertEqual(report.operations[0].result.exit_code, 0)

    def test_nonzero_exit_and_timeout_never_report_success(self):
        self.exit_code = 4
        for error in (None, OperationError(ErrorCode.TIMEOUT, "Native timeout.")):
            self.command_error = error
            report = self.execute(self.preview())
            self.assertEqual(report.operations[0].state, State.FAILED)
            self.assertEqual(report.operations[0].result.exit_code, 4)

    def test_partial_failure_propagates_through_skipped_dependencies(self):
        alpha, beta, gamma, other = self.packages
        packages = (
            replace(alpha, dependencies=(Dependency("beta"),)),
            replace(beta, dependencies=(Dependency("gamma"),)),
            gamma,
            other,
        )
        plan = self.preview(
            PackageRequest("alpha"), PackageRequest("other"), packages=packages
        )
        real = self.runner.run.side_effect

        def run(command, **kwargs):
            self.exit_code = 1 if command.argv[-1] == "gamma" else 0
            return real(command, **kwargs)

        self.runner.run.side_effect = run
        report = self.execute(plan)
        self.assertEqual(
            [o.state for o in report.operations],
            [State.FAILED, State.SKIPPED, State.SKIPPED, State.SUCCEEDED],
        )
        self.assertEqual(self.runner.run.call_count, 2)

    def test_cancellation_before_start_during_command_and_between_items(self):
        cancel = threading.Event()
        plan = self.preview(PackageRequest("alpha"), PackageRequest("beta"))
        cancel.set()
        self.assertTrue(
            all(
                o.state == State.CANCELLED
                for o in self.execute(plan, cancel=cancel).operations
            )
        )
        self.runner.run.assert_not_called()
        cancel.clear()
        self.command_error = OperationError(ErrorCode.CANCELLED, "Native cancellation.")
        real = self.runner.run.side_effect

        def run(command, **kwargs):
            cancel.set()
            return real(command, **kwargs)

        self.runner.run.side_effect = run
        report = self.execute(plan, cancel=cancel)
        self.assertEqual(
            [o.state for o in report.operations], [State.CANCELLED, State.CANCELLED]
        )
        self.assertEqual(self.runner.run.call_count, 1)

    def test_uninstall_upgrade_and_read_only_verify(self):
        self.install("alpha", "1.0")
        report = self.execute(self.preview(PackageRequest("alpha", Action.UPGRADE)))
        self.assertEqual(report.operations[0].state, State.SUCCEEDED)
        report = self.execute(
            self.preview(PackageRequest("alpha", Action.VERIFY)), consent=None
        )
        self.assertEqual(report.operations[0].state, State.SUCCEEDED)
        report = self.execute(self.preview(PackageRequest("alpha", Action.UNINSTALL)))
        self.assertEqual(report.operations[0].state, State.SUCCEEDED)
        self.assertNotIn("alpha", self.inspector.installed)
        self.assertEqual(self.runner.run.call_count, 2)

    def test_up_to_date_upgrade_skips_and_uninstalled_upgrade_fails(self):
        self.assertFalse(
            self.preview(PackageRequest("alpha", Action.UPGRADE)).plan.ready
        )
        self.install("alpha", "2.0")
        report = self.execute(
            self.preview(PackageRequest("alpha", Action.UPGRADE)), consent=None
        )
        self.assertEqual(report.operations[0].state, State.SKIPPED)
        self.runner.run.assert_not_called()

    def test_native_dependency_can_satisfy_later_install(self):
        plan = self.preview(PackageRequest("alpha"), PackageRequest("beta"))
        real = self.runner.run.side_effect

        def run(command, **kwargs):
            self.install("beta", "2.0")
            return real(command, **kwargs)

        self.runner.run.side_effect = run
        report = self.execute(plan)
        self.assertEqual(
            [o.state for o in report.operations], [State.SUCCEEDED, State.SKIPPED]
        )
        self.assertEqual(self.runner.run.call_count, 1)

    def test_observers_cannot_break_execution_or_expose_exception_values(self):
        def broken(*args):
            raise RuntimeError("secret exception text")

        report = self.execute(self.preview(), observe=broken, logs=broken)
        self.assertEqual(report.operations[0].state, State.SUCCEEDED)
        self.assertNotIn("secret exception", repr(report))

    def test_internal_execution_failure_is_sanitized(self):
        self.runner.run.side_effect = RuntimeError("password=do-not-expose")
        report = self.execute(self.preview())
        self.assertEqual(report.operations[0].result.error.code, ErrorCode.UNKNOWN)
        self.assertNotIn("do-not-expose", repr(report))

    def test_invalid_plan_cannot_execute_and_stale_skip_cannot_mutate(self):
        with self.assertRaises(ValueError):
            self.execute(self.preview(PackageRequest("unknown")))
        self.install("alpha")
        plan = self.preview()
        self.inspector.installed.clear()
        self.assertEqual(self.execute(plan).operations[0].state, State.FAILED)
        self.runner.run.assert_not_called()

    def test_inventory_aggregates_failures_and_executable_presence_separately(self):
        service = InventoryService((self.inspector,), self.runner)
        self.inspector.error = ErrorCode.NETWORK
        with mock.patch("services.inventory.shutil.which", return_value=None):
            report = service.collect((self.packages[0],))
        self.assertFalse(report.snapshots)
        self.assertEqual(report.errors[0][1].code, ErrorCode.NETWORK)
        self.assertFalse(report.detections[0].present)
        self.inspector.error = None
        command_result = CommandResult(
            mock.Mock(), utc_now(), utc_now(), 0, "git version 2.0"
        )
        self.runner.run.side_effect = None
        self.runner.run.return_value = command_result
        with mock.patch(
            "services.inventory.shutil.which", return_value=str(self.root / "git")
        ):
            report = service.collect((self.packages[0],))
        self.assertTrue(report.detections[0].present)
        self.assertEqual(report.detections[0].output, "git version 2.0")
        self.assertIsNone(report.snapshots[0].get("alpha"))
        self.assertFalse(self.runner.run.call_args.args[0].mutates)

    def test_record_validation_rejects_ambiguous_inventory_and_implicit_consent(self):
        with self.assertRaises(ValueError):
            Inventory(
                "homebrew",
                (InstalledPackage("alpha", ("1",)), InstalledPackage("alpha", ("2",))),
            )
        with self.assertRaises(ValueError):
            PlanConsent(self.preview(), 1)
