"""Opt-in harness contracts are tested entirely with fake application services."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from models.inventory import InstalledPackage, Inventory, NativeReview
from models.package import PackageVersion
from models.planning import OperationAction, utc_now
from models.platform import (
    Architecture,
    LinuxDistribution,
    LinuxFamily,
    OperatingSystem,
    Platform,
)
from package_managers.apt import APT
from scripts.qualify_native import (
    apt_upgrade_baseline,
    disposable_guard,
    exercise,
    main,
    manager_version,
)
from services.catalog.service import CatalogService
from services.desktop import DesktopServices
from system.commands import CommandResult
from tests.fixtures.desktop import DesktopFixture


class NativeQualificationTests(unittest.TestCase):
    def apt_fixture(self, root, package_id="git"):
        fixture = DesktopFixture(root)
        for name in ("apt-get", "apt-cache", "dpkg-query", "pkexec", "dpkg"):
            (root / name).write_bytes(b"Synthetic native executable; never executed.")
        host = Platform(
            OperatingSystem.LINUX,
            Architecture.X86_64,
            LinuxDistribution("ubuntu", LinuxFamily.DEBIAN, version="24.04"),
        )
        fixture.adapter = APT(host, find=lambda name: str(root / name))
        fixture.inventory = lambda cancel=None: Inventory(
            "apt", tuple(fixture.installed.values())
        )
        fixture.review = lambda spec, action, cancel=None: NativeReview(
            None
            if action == OperationAction.UNINSTALL
            else spec.version.value
            if spec.version
            else "2.0",
            "reviewed-native-metadata",
            ("Reviewed native transaction.",),
        )

        def run(command, **kwargs):
            if command.mutates:
                fixture.mutations.append(command)
                name, _, version = command.argv[-1].partition("=")
                if "remove" in command.argv:
                    fixture.installed.pop(name, None)
                else:
                    fixture.installed[name] = InstalledPackage(
                        name, (version or "2.0",)
                    )
            now = utc_now()
            return CommandResult(command.preview(), now, now, 0)

        fixture.runner.run.side_effect = run
        package = fixture.services.catalog.get(package_id)
        fixture.services = DesktopServices(
            CatalogService((package,)),
            host,
            (fixture.adapter,),
            (fixture,),
            fixture.runner,
            require_native_qualification=False,
        )
        return fixture

    def test_explicit_baseline_requires_and_verifies_a_real_version_change(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.apt_fixture(Path(directory))
            checks = {}
            exercise(
                fixture.services, "git", checks, baseline_version=PackageVersion("1.0")
            )
            self.assertEqual(checks["real_version_upgrade"], "passed")
            self.assertEqual(
                (checks["upgrade_from"], checks["upgrade_to"]), ("1.0", "2.0")
            )
            self.assertTrue(
                any("--only-upgrade" in command.argv for command in fixture.mutations)
            )
            self.assertFalse(fixture.installed)

    def test_current_version_baseline_cannot_be_reported_as_upgrade_success(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = self.apt_fixture(Path(directory))
            checks = {}
            with self.assertRaises(ValueError):
                exercise(
                    fixture.services,
                    "git",
                    checks,
                    baseline_version=PackageVersion("2.0"),
                )
            self.assertNotEqual(checks.get("real_version_upgrade"), "passed")
            self.assertFalse(
                any("--only-upgrade" in command.argv for command in fixture.mutations)
            )

    def test_upgrade_baseline_uses_native_ordering_and_revalidates_exact_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = self.apt_fixture(root)
            inspector = mock.Mock()
            inspector.adapter = fixture.adapter
            inspector.runner = fixture.runner
            inspector.review.return_value = NativeReview("2.0", "reviewed", ())
            inspector.read.return_value = "git | 1.0 | official native entry"
            with mock.patch(
                "scripts.qualify_native.shutil.which", return_value=str(root / "dpkg")
            ):
                self.assertEqual(
                    apt_upgrade_baseline(inspector, fixture.services),
                    PackageVersion("1.0"),
                )
            compared = fixture.runner.run.call_args.args[0]
            self.assertEqual(
                compared.argv[1:], ("--compare-versions", "1.0", "lt", "2.0")
            )
            self.assertEqual(
                inspector.review.call_args.args[0].version, PackageVersion("1.0")
            )

    def test_guard_requires_hosted_scope_approval_and_exact_platform(self):
        ubuntu = Platform(
            OperatingSystem.LINUX,
            Architecture.X86_64,
            LinuxDistribution("ubuntu", LinuxFamily.DEBIAN, version="24.04"),
        )
        mac = Platform(OperatingSystem.MACOS, Architecture.ARM64)
        hosted = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted"}
        disposable_guard(
            "apt", ubuntu, hosted, container=True, root=True, approved=True
        )
        disposable_guard(
            "homebrew", mac, hosted, container=False, root=False, approved=True
        )
        disposable_guard(
            "apt",
            ubuntu,
            {"DEV_SETUP_DISPOSABLE_CONTAINER": "1"},
            container=True,
            root=True,
            approved=True,
        )
        for backend, host, env, container, root, approved in (
            ("apt", ubuntu, hosted, True, True, False),
            ("apt", ubuntu, {}, True, True, True),
            (
                "apt",
                ubuntu,
                {**hosted, "RUNNER_ENVIRONMENT": "self-hosted"},
                True,
                True,
                True,
            ),
            ("apt", ubuntu, hosted, False, True, True),
            ("homebrew", mac, hosted, False, True, True),
            ("homebrew", ubuntu, hosted, True, False, True),
            ("apt", ubuntu, {"DEV_SETUP_DISPOSABLE_CONTAINER": "1"}, False, True, True),
            (
                "homebrew",
                mac,
                {"DEV_SETUP_DISPOSABLE_CONTAINER": "1"},
                False,
                False,
                True,
            ),
        ):
            with (
                self.subTest(backend=backend, host=host, env=env),
                self.assertRaises(ValueError),
            ):
                disposable_guard(
                    backend,
                    host,
                    env,
                    container=container,
                    root=root,
                    approved=approved,
                )

    def test_guard_rejection_precedes_file_creation_or_command_construction(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "must-not-exist.json"
            with (
                mock.patch(
                    "scripts.qualify_native.disposable_guard",
                    side_effect=ValueError("private-token"),
                ),
                mock.patch("scripts.qualify_native.CommandRunner") as runner,
                mock.patch("builtins.print") as printed,
            ):
                result = main(
                    [
                        "--backend",
                        "apt",
                        "--approve-native-changes",
                        "--output",
                        str(output),
                    ]
                )
            self.assertEqual(result, 2)
            self.assertFalse(output.exists())
            runner.assert_not_called()
            self.assertNotIn("private-token", repr(printed.call_args_list))

    def test_smoke_exercises_real_workflow_logic_but_does_not_claim_upgrade_qualification(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            fixture = DesktopFixture(Path(directory))
            fixture.install()  # The explicit disposable scope includes cleanup.
            checks = {}
            exercise(fixture.services, "git", checks)
            self.assertEqual(checks["phase"], "complete")
            self.assertEqual(checks["install_verify_refresh"], "passed")
            self.assertEqual(checks["stale_inventory_plan"], "passed")
            self.assertEqual(checks["real_version_upgrade"], "not_tested")
            self.assertEqual(checks["mid_command_cancellation"], "not_tested")
            self.assertEqual(
                [command.argv[1] for command in fixture.mutations],
                ["uninstall", "install", "uninstall"],
            )
            self.assertFalse(fixture.installed)

    def test_smoke_stops_on_failed_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = DesktopFixture(Path(directory))
            fixture.mutate = False
            checks = {}
            with self.assertRaises(ValueError):
                exercise(fixture.services, "git", checks)
            self.assertEqual(checks["phase"], "install_and_verify")
            self.assertNotIn("remove_verify", checks)

    def test_manager_version_extracts_only_bounded_native_version(self):
        self.assertEqual(
            manager_version("apt", "apt 2.8.3 (amd64)\nother details"), "2.8.3"
        )
        self.assertEqual(
            manager_version("homebrew", "Homebrew 4.6.2\nlocal paths"), "4.6.2"
        )
        with self.assertRaises(ValueError) as caught:
            manager_version("apt", "password=private-token")
        self.assertNotIn("private-token", str(caught.exception))
