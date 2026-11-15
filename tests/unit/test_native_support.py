"""Qualification policy is enforced beyond button state; no native execution."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from models.errors import ErrorCode
from models.operations import OperationState
from models.planning import OperationAction
from services.desktop import DesktopServices
from services.records import PackageRequest, PlanConsent
from services.support import SupportLevel, workflow_support
from tests.fixtures.desktop import DesktopFixture


class NativeSupportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.fixture = DesktopFixture(Path(directory.name))
        base = self.fixture.services
        self.services = DesktopServices(
            base.catalog, base.host, base.adapters, base.inspectors, self.fixture.runner
        )
        self.spec = next(
            s for s in base.catalog.get("git").install_specs if s.backend == "homebrew"
        )

    def test_default_desktop_policy_blocks_each_unqualified_mutation_before_inspection(
        self,
    ):
        for action in (
            OperationAction.INSTALL,
            OperationAction.UPGRADE,
            OperationAction.UNINSTALL,
        ):
            with self.subTest(action=action):
                support = self.services.workflow_support(self.spec, action)
                self.assertEqual(support.level, SupportLevel.UNQUALIFIED)
                self.assertIn(
                    "qualification", self.services.action_reason(self.spec, action)
                )
                self.assertFalse(self.services.version_selection(self.spec, action))
                with mock.patch.object(
                    self.fixture,
                    "inventory",
                    side_effect=AssertionError("must not inspect"),
                ):
                    plan = self.services.planner.preview(
                        (PackageRequest("git", action),)
                    )
                self.assertFalse(plan.plan.ready)
                self.assertTrue(
                    any(
                        issue.code == ErrorCode.UNSUPPORTED_OPERATION
                        for issue in plan.plan.issues
                    )
                )
        self.fixture.runner.run.assert_not_called()

    def test_executor_rechecks_policy_for_plan_from_experimental_caller(self):
        plan = self.fixture.services.planner.preview((PackageRequest("git"),))
        report = self.services.operations.execute(
            plan, consent=PlanConsent(plan, True, True)
        )
        self.assertEqual(report.operations[0].state, OperationState.FAILED)
        self.assertEqual(
            report.operations[0].result.error.code, ErrorCode.UNSUPPORTED_OPERATION
        )
        self.assertFalse(self.fixture.mutations)
        self.fixture.runner.run.assert_not_called()

    def test_read_only_inventory_and_verify_remain_available(self):
        self.fixture.install()
        self.assertIsNone(
            self.services.action_reason(self.spec, OperationAction.VERIFY)
        )
        self.assertEqual(
            self.services.workflow_support(self.spec, OperationAction.VERIFY).level,
            SupportLevel.READ_ONLY,
        )
        plan = self.services.planner.preview(
            (PackageRequest("git", OperationAction.VERIFY),)
        )
        self.assertTrue(plan.plan.ready)
        report = self.services.operations.execute(plan)
        self.assertEqual(report.operations[0].state, OperationState.SUCCEEDED)
        self.assertFalse(self.fixture.mutations)

    def test_command_contract_does_not_advertise_inventory_or_native_workflow(self):
        self.assertEqual(
            workflow_support(
                "winget", OperationAction.INSTALL, inspector=False, command=True
            ).level,
            SupportLevel.COMMAND_ONLY,
        )
        self.assertEqual(
            workflow_support(
                "winget", OperationAction.VERIFY, inspector=False, command=False
            ).level,
            SupportLevel.UNSUPPORTED,
        )
        self.assertEqual(
            workflow_support(
                "pacman", OperationAction.UPGRADE, inspector=True, command=False
            ).level,
            SupportLevel.UNSUPPORTED,
        )

    def test_missing_manager_reason_is_distinct_from_pending_qualification(self):
        with mock.patch.object(self.fixture.adapter, "find", return_value=None):
            self.assertIn(
                "missing",
                self.services.action_reason(self.spec, OperationAction.INSTALL),
            )
