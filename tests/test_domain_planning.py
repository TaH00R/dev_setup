"""Review records are validated without resolving or executing package commands."""

import unittest
from dataclasses import replace
from datetime import datetime
from unittest import mock

from models.errors import ErrorCode, OperationError
from models.package import PackageSource, SourceKind, TrustLevel
from models.planning import (
    CommandPreview,
    InstallPlan,
    OperationAction,
    PlanItem,
)
from models.platform import Architecture, OperatingSystem, Platform
from models.specs import InstallSpec, PlatformSupport


def example_item(package_id="example.tool", **kwargs):
    source = PackageSource("example", "Example", SourceKind.CURATED, TrustLevel.UNKNOWN)
    support = PlatformSupport(OperatingSystem.WINDOWS, (Architecture.X86_64,))
    spec = InstallSpec(support, "example", "Example.Tool", source)
    return PlanItem(
        package_id,
        OperationAction.INSTALL,
        spec,
        (
            CommandPreview(
                ("example-manager", "install", "Example.Tool"), "Install tool"
            ),
        ),
        **kwargs,
    )


class PlanningModelTests(unittest.TestCase):
    def setUp(self):
        self.host = Platform(OperatingSystem.WINDOWS, Architecture.X86_64)
        self.item = example_item()

    def test_building_plan_is_pure_and_does_not_grant_consent(self):
        with mock.patch(
            "subprocess.Popen", side_effect=AssertionError("unexpected process")
        ):
            plan = InstallPlan("example-plan", self.host, (self.item,))
        self.assertTrue(plan.ready)
        self.assertTrue(plan.requires_consent)
        self.assertIsNotNone(plan.created_at.utcoffset())

    def test_empty_and_issue_bearing_plans_are_not_ready(self):
        self.assertFalse(InstallPlan("empty", self.host, ()).ready)
        issue = OperationError(
            ErrorCode.MISSING_BACKEND, "Example manager is absent.", True
        )
        plan = InstallPlan("blocked", self.host, (self.item,), (issue,))
        self.assertFalse(plan.ready)
        self.assertEqual(plan.issues[0].code, ErrorCode.MISSING_BACKEND)

    def test_dependencies_must_exist_and_precede_dependents(self):
        dependency = example_item("example.runtime")
        dependent = replace(self.item, dependencies=(dependency.package_id,))
        self.assertTrue(
            InstallPlan("ordered", self.host, (dependency, dependent)).ready
        )
        for items in [(dependent,), (dependent, dependency), (self.item, self.item)]:
            with self.subTest(items=items), self.assertRaises(ValueError):
                InstallPlan("invalid", self.host, items)

    def test_unknown_platform_and_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            InstallPlan(
                "wrong-host",
                Platform(OperatingSystem.MACOS, Architecture.ARM64),
                (self.item,),
            )
        with self.assertRaises(ValueError):
            InstallPlan(
                "naive", self.host, (self.item,), created_at=datetime(2026, 1, 1)
            )

    def test_skips_and_verification_do_not_require_mutation_consent(self):
        skip = replace(self.item, commands=(), skip_reason="Already installed.")
        self.assertFalse(skip.requires_consent)
        self.assertFalse(
            replace(self.item, action=OperationAction.VERIFY).requires_consent
        )
        with self.assertRaises(ValueError):
            replace(self.item, commands=())
        with self.assertRaises(ValueError):
            replace(self.item, skip_reason="Already installed.")

    def test_invalid_command_and_error_records(self):
        for args in [(), ["example"], ("example", "bad\nargument")]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                CommandPreview(args, "Example")
        with self.assertRaises(ValueError):
            OperationError("timeout", "Timed out")
        with self.assertRaises(ValueError):
            OperationError(ErrorCode.TIMEOUT, "Timed out", retryable="yes")
