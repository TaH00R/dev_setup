"""Review identity catches replacement, PATH changes, helpers and Scoop scripts."""

import os
import platform
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from models.errors import ErrorCode
from models.package import PackageSource, SourceKind, TrustLevel
from models.platform import (
    Architecture,
    LinuxDistribution,
    LinuxFamily,
    OperatingSystem,
    Platform,
)
from package_managers.apt import APT
from package_managers.scoop import Scoop
from services.records import PackageRequest, PlanConsent
from system.commands import Approval, Command
from system.coordination import NativeCoordinator
from system.identity import IdentityError, bind, revalidate
from system.platform import normalize_os
from system.runner import CommandRunner
from tests.fixtures.desktop import DesktopFixture


class LaunchIdentityTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.fixture = DesktopFixture(self.root)

    def test_changed_executable_requires_new_review_before_native_dispatch(self):
        plan = self.fixture.services.planner.preview((PackageRequest("git"),))
        executable = self.root / "brew"
        previous = executable.stat()
        executable.write_bytes(b"x" * previous.st_size)
        os.utime(executable, ns=(previous.st_atime_ns, previous.st_mtime_ns))
        report = self.fixture.services.operations.execute(
            plan, consent=PlanConsent(plan, True)
        )
        self.assertEqual(
            report.operations[0].result.error.code, ErrorCode.VERIFICATION_FAILED
        )
        self.assertFalse(self.fixture.mutations)
        fresh = self.fixture.services.planner.preview((PackageRequest("git"),))
        self.assertEqual(
            self.fixture.services.operations.execute(
                fresh, consent=PlanConsent(fresh, True)
            ).outcome.value,
            "succeeded",
        )

    def test_path_resolution_changes_after_review_are_rejected(self):
        dirs = [self.root / "a", self.root / "b"]
        name = "brew.exe" if os.name == "nt" else "brew"
        for directory in dirs:
            directory.mkdir()
            executable = directory / name
            executable.write_bytes(b"Synthetic executable")
            executable.chmod(0o700)
        self.fixture.adapter.find = lambda _: shutil.which(name)
        with mock.patch.dict(os.environ, {"PATH": str(dirs[0])}):
            plan = self.fixture.services.planner.preview((PackageRequest("git"),))
        with mock.patch.dict(os.environ, {"PATH": str(dirs[1])}):
            report = self.fixture.services.operations.execute(
                plan, consent=PlanConsent(plan, True)
            )
        self.assertEqual(report.operations[0].state.value, "failed")
        self.assertFalse(self.fixture.mutations)

    def test_runner_rechecks_bound_identity_and_refuses_unbound_mutations(self):
        path = self.root / "native.exe"
        path.write_bytes(b"reviewed")
        command = Command(
            (str(path),),
            "Synthetic reviewed mutation",
            normalize_os(platform.system()),
            "fixture",
            mutates=True,
        )
        runner = CommandRunner(coordinator=NativeCoordinator(self.root / "locks"))
        with mock.patch("system.runner.subprocess.Popen") as spawn:
            self.assertEqual(
                runner.run(command, approval=Approval(command)).error.code,
                ErrorCode.VERIFICATION_FAILED,
            )
            reviewed = bind(command)
            path.write_bytes(b"replaced")
            self.assertEqual(
                runner.run(reviewed, approval=Approval(reviewed)).error.code,
                ErrorCode.VERIFICATION_FAILED,
            )
        spawn.assert_not_called()

    def test_elevation_helper_and_delegated_executable_are_both_pinned(self):
        helper, native = self.root / "helper", self.root / "native"
        helper.write_bytes(b"reviewed helper")
        native.write_bytes(b"reviewed native")
        reviewed = bind(
            Command(
                (str(helper), str(native), "install"),
                "Synthetic elevation",
                OperatingSystem.LINUX,
                "fixture",
                mutates=True,
                launch_paths=(str(native),),
            )
        )
        self.assertEqual(len(reviewed.launch_files), 2)
        native.write_bytes(b"changed native")
        with self.assertRaises(IdentityError):
            revalidate(reviewed)

    def test_apt_adapter_includes_native_executable_behind_elevation_helper(self):
        for name in ("apt-get", "pkexec"):
            (self.root / name).write_bytes(b"Reviewed synthetic launch file")
        host = Platform(
            OperatingSystem.LINUX,
            Architecture.X86_64,
            LinuxDistribution("ubuntu", LinuxFamily.DEBIAN, version="24.04"),
        )
        adapter = APT(host, find=lambda name: str(self.root / name))
        package = next(
            package
            for package in self.fixture.services.catalog.packages
            if package.id == "git"
        )
        spec = next(spec for spec in package.install_specs if spec.backend == "apt")
        with mock.patch(
            "package_managers.apt.os.geteuid", return_value=1000, create=True
        ):
            command = bind(adapter.install(spec))
        self.assertEqual(
            {Path(item.requested).name for item in command.launch_files},
            {"pkexec", "apt-get"},
        )
        (self.root / "apt-get").write_bytes(b"replaced apt-get")
        with self.assertRaises(IdentityError):
            revalidate(command)

    def test_symlink_retargeting_invalidates_review_even_when_bytes_match(self):
        first, second, link = self.root / "one", self.root / "two", self.root / "link"
        first.write_bytes(b"identical bytes")
        second.write_bytes(b"identical bytes")
        try:
            link.symlink_to(first)
        except OSError:
            self.skipTest("Host does not permit symlink creation.")
        reviewed = bind(
            Command((str(link),), "Symlink fixture", OperatingSystem.LINUX, "fixture")
        )
        link.unlink()
        link.symlink_to(second)
        with self.assertRaises(IdentityError):
            revalidate(reviewed)

    @unittest.skipIf(os.name == "nt", "POSIX shebang launch behavior")
    def test_env_shebang_path_selection_is_part_of_reviewed_identity(self):
        script = self.root / "tool"
        script.write_text("#!/usr/bin/env fixture-interpreter\n")
        dirs = [self.root / "a", self.root / "b"]
        for directory in dirs:
            directory.mkdir()
            interpreter = directory / "fixture-interpreter"
            interpreter.write_bytes(b"synthetic native interpreter")
            interpreter.chmod(0o700)
        command = Command(
            (str(script),), "Interpreter fixture", OperatingSystem.LINUX, "fixture"
        )
        with mock.patch.dict(os.environ, {"PATH": str(dirs[0])}):
            reviewed = bind(command)
        with mock.patch.dict(os.environ, {"PATH": str(dirs[1])}):
            revalidate(reviewed, environment={"PATH": str(dirs[0])})
            with self.assertRaises(IdentityError):
                revalidate(reviewed)

    def test_scoop_bridge_entry_script_and_powershell_identity_are_revalidated(self):
        for name in ("pwsh.exe", "scoop.ps1", "scoop_bridge.ps1"):
            (self.root / name).write_bytes(b"Reviewed synthetic launch asset")
        manager = Scoop(
            Platform(OperatingSystem.WINDOWS, Architecture.X86_64),
            find=lambda name: str(self.root / name),
        )
        template = next(
            package
            for package in self.fixture.services.catalog.packages
            if package.id == "git"
        )
        spec = replace(
            next(spec for spec in template.install_specs if spec.backend == "winget"),
            backend="scoop",
            native_id="git",
            source=PackageSource(
                "scoop-main",
                "Scoop main",
                SourceKind.CURATED,
                TrustLevel.COMMUNITY_REPOSITORY,
                "https://github.com/ScoopInstaller/Main",
            ),
        )
        with mock.patch("package_managers.scoop.__file__", str(self.root / "scoop.py")):
            for name in ("pwsh.exe", "scoop.ps1", "scoop_bridge.ps1"):
                reviewed = bind(manager.install(spec))
                self.assertEqual(len(reviewed.launch_files), 3)
                self.assertNotIn("-Command", reviewed.argv)
                self.assertNotIn("-ExecutionPolicy", reviewed.argv)
                (self.root / name).write_bytes(b"Changed launch asset")
                with self.assertRaises(IdentityError):
                    revalidate(reviewed)
