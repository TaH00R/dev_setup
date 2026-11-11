"""Exact native command contracts; every package operation is mocked."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from catalog.loader import load_catalog
from catalog.service import CatalogService
from models.errors import ErrorCode
from models.package import PackageSource, PackageVersion, SourceKind, TrustLevel
from models.platform import (
    Architecture,
    LinuxDistribution,
    LinuxFamily,
    OperatingSystem,
    Platform,
)
from package_managers.apt import APT
from package_managers.base import AdapterError, BackendAction
from package_managers.homebrew import Homebrew
from package_managers.registry import adapters_for
from package_managers.scoop import Scoop
from package_managers.winget import WinGet
from system.commands import Approval
from system.runner import CommandRunner


class AdapterTests(unittest.TestCase):
    def test_scoop_script_discovery_does_not_depend_on_pathext_or_batch_shims(self):
        with (
            mock.patch.dict(
                "os.environ", {"PATH": str(self.root), "PATHEXT": ".EXE;.CMD"}
            ),
            mock.patch("package_managers.scoop.shutil.which", return_value=None),
        ):
            manager = Scoop(self.windows)
            self.assertEqual(manager.find("scoop.ps1"), str(self.root / "scoop.ps1"))

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "scoop.ps1").write_text(
            "# never executed in these tests", encoding="utf-8"
        )
        self.find = lambda name: str(self.root / name)
        self.windows = Platform(OperatingSystem.WINDOWS, Architecture.X86_64)
        self.macos = Platform(OperatingSystem.MACOS, Architecture.ARM64)
        self.linux = Platform(
            OperatingSystem.LINUX,
            Architecture.X86_64,
            LinuxDistribution("ubuntu", LinuxFamily.DEBIAN, version="24.04"),
        )
        self.service = CatalogService(load_catalog())
        self.winget = WinGet(self.windows, find=self.find)
        self.brew = Homebrew(self.macos, find=self.find)
        self.apt = APT(self.linux, find=self.find)
        self.scoop = Scoop(self.windows, find=self.find)

    def spec(self, package_id, backend):
        return next(
            spec
            for spec in self.service.get(package_id).install_specs
            if spec.backend == backend
        )

    def scoop_spec(self):
        source = PackageSource(
            "scoop-main",
            "Scoop main",
            SourceKind.CURATED,
            TrustLevel.COMMUNITY_REPOSITORY,
            "https://github.com/ScoopInstaller/Main",
        )
        return replace(
            self.spec("git", "winget"), backend="scoop", native_id="git", source=source
        )

    def test_winget_exact_source_and_native_id_for_mutations(self):
        spec = self.spec("git", "winget")
        for action in (
            BackendAction.INSTALL,
            BackendAction.UNINSTALL,
            BackendAction.UPGRADE,
        ):
            with self.subTest(action=action):
                command = self.winget.build(action, spec=spec)
                self.assertEqual(
                    command.argv,
                    (
                        self.find("winget.exe"),
                        action.value,
                        "--id",
                        "Git.Git",
                        "--exact",
                        "--source",
                        "winget",
                        "--disable-interactivity",
                    ),
                )
                self.assertTrue(command.requires_consent)
                self.assertNotIn("--accept-package-agreements", command.argv)
                self.assertNotIn("--accept-source-agreements", command.argv)

    def test_winget_version_search_metadata_and_inventory(self):
        self.assertEqual(
            self.winget.version().argv, (self.find("winget.exe"), "--version")
        )
        self.assertEqual(
            self.winget.search("visual studio").argv,
            (
                self.find("winget.exe"),
                "search",
                "--query",
                "visual studio",
                "--source",
                "winget",
                "--disable-interactivity",
            ),
        )
        self.assertEqual(
            self.winget.list_installed().argv,
            (
                self.find("winget.exe"),
                "list",
                "--source",
                "winget",
                "--disable-interactivity",
            ),
        )
        spec = replace(self.spec("git", "winget"), version=PackageVersion("2.50.0"))
        self.assertIn("--version", self.winget.show(spec).argv)
        with self.assertRaises(AdapterError):
            self.winget.uninstall(spec)

    def test_homebrew_keeps_cask_and_formula_explicit(self):
        for package, kind in (("git", "--formula"), ("vscode", "--cask")):
            spec = self.spec(package, "homebrew")
            for action in (
                BackendAction.INSTALL,
                BackendAction.UNINSTALL,
                BackendAction.UPGRADE,
            ):
                command = self.brew.build(action, spec=spec)
                self.assertEqual(
                    command.argv,
                    (self.find("brew"), action.value, kind, "--", spec.native_id),
                )
                self.assertEqual(
                    dict(command.environment)["HOMEBREW_NO_AUTO_UPDATE"], "1"
                )
            self.assertEqual(
                self.brew.show(spec).argv,
                (self.find("brew"), "info", kind, "--json=v2", "--", spec.native_id),
            )
        self.assertEqual(
            self.brew.search("git").argv, (self.find("brew"), "search", "--", "git")
        )
        self.assertEqual(
            self.brew.list_installed().argv, (self.find("brew"), "list", "--versions")
        )

    def test_apt_targets_one_exact_package_and_never_updates_the_host(self):
        spec = self.spec("git", "apt")
        with mock.patch(
            "package_managers.apt.os.geteuid", return_value=1000, create=True
        ):
            for action, args in [
                (BackendAction.INSTALL, ("install",)),
                (BackendAction.UNINSTALL, ("remove",)),
                (BackendAction.UPGRADE, ("install", "--only-upgrade")),
            ]:
                with self.subTest(action=action):
                    command = self.apt.build(action, spec=spec)
                    self.assertEqual(
                        command.argv,
                        (
                            self.find("pkexec"),
                            "--disable-internal-agent",
                            self.find("apt-get"),
                            "--yes",
                            "--target-release",
                            "noble",
                        )
                        + args
                        + ("--", "git"),
                    )
                    self.assertNotIn("update", command.argv)
                    self.assertNotIn("dist-upgrade", command.argv)
                    self.assertNotIn("purge", command.argv)
        with mock.patch("package_managers.apt.os.geteuid", return_value=0, create=True):
            command = self.apt.install(spec)
            self.assertEqual(command.argv[0], self.find("apt-get"))

    def test_apt_cache_inventory_and_missing_native_elevation(self):
        self.assertEqual(
            self.apt.search("libc++").argv,
            (self.find("apt-cache"), "search", "--names-only", "--", r"libc\+\+"),
        )
        self.assertEqual(
            self.apt.show(self.spec("git", "apt")).argv,
            (self.find("apt-cache"), "show", "--", "git"),
        )
        self.assertEqual(
            self.apt.list_installed().argv,
            (
                self.find("dpkg-query"),
                "--show",
                r"--showformat=${binary:Package}\t${Version}\t${db:Status-Status}\n",
            ),
        )
        missing = APT(
            self.linux, find=lambda name: None if name == "pkexec" else self.find(name)
        )
        with (
            mock.patch(
                "package_managers.apt.os.geteuid", return_value=1000, create=True
            ),
            self.assertRaises(AdapterError) as caught,
        ):
            missing.install(self.spec("git", "apt"))
        self.assertEqual(caught.exception.error.code, ErrorCode.PERMISSION_DENIED)

    def test_scoop_bridge_is_fixed_and_source_qualified(self):
        spec = self.scoop_spec()
        for action in BackendAction:
            command = self.scoop.build(
                action,
                spec=spec
                if action
                in {
                    BackendAction.INSTALL,
                    BackendAction.UNINSTALL,
                    BackendAction.UPGRADE,
                    BackendAction.SHOW,
                }
                else None,
                query="git" if action == BackendAction.SEARCH else None,
            )
            self.assertEqual(
                command.argv[:5],
                (
                    self.find("pwsh.exe"),
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-File",
                ),
            )
            self.assertEqual(Path(command.argv[5]).name, "scoop_bridge.ps1")
            self.assertNotIn("-Command", command.argv)
            self.assertNotIn("-ExecutionPolicy", command.argv)
            if action in {
                BackendAction.INSTALL,
                BackendAction.UNINSTALL,
                BackendAction.UPGRADE,
                BackendAction.SHOW,
            }:
                self.assertEqual(command.argv[-2:], ("-Package", "main/git"))
                if action != BackendAction.SHOW:
                    self.assertEqual(command.privilege, spec.privilege)

    def test_rejects_ambiguous_sources_native_paths_urls_channels_and_queries(self):
        for manager, spec, bad_id in [
            (self.winget, self.spec("git", "winget"), "repo/pkg"),
            (self.brew, self.spec("git", "homebrew"), "https://example.org/tool"),
            (self.apt, self.spec("git", "apt"), "git/bad"),
            (self.scoop, self.scoop_spec(), "main/git"),
        ]:
            with self.subTest(manager=manager.id):
                with self.assertRaises(AdapterError):
                    manager.install(replace(spec, native_id=bad_id))
                with self.assertRaises(AdapterError):
                    manager.install(
                        replace(spec, source=replace(spec.source, id="unreviewed"))
                    )
                with self.assertRaises(AdapterError):
                    manager.install(
                        replace(spec, version=PackageVersion("1", "unstable"))
                    )
                for query in (
                    "--help",
                    "*",
                    "a;command",
                    "$(command)",
                    "x\n",
                    "x" * 101,
                ):
                    with self.subTest(query=query), self.assertRaises(AdapterError):
                        manager.search(query)

    def test_capabilities_reject_unsupported_versions_and_mappings(self):
        with self.assertRaises(AdapterError):
            self.brew.install(
                replace(self.spec("git", "homebrew"), version=PackageVersion("1"))
            )
        with self.assertRaises(AdapterError):
            self.scoop.install(replace(self.scoop_spec(), version=PackageVersion("1")))
        with self.assertRaises(AdapterError):
            self.winget.install(self.spec("git", "apt"))

    def test_availability_and_registry_are_platform_specific_without_processes(self):
        with mock.patch(
            "subprocess.Popen", side_effect=AssertionError("unexpected execution")
        ):
            self.assertEqual(
                tuple(
                    adapter.id for adapter in adapters_for(self.windows, find=self.find)
                ),
                ("winget", "scoop"),
            )
            fedora = replace(
                self.linux, distribution=LinuxDistribution("fedora", LinuxFamily.FEDORA)
            )
            self.assertEqual(adapters_for(fedora, find=self.find), ())
            self.assertFalse(
                WinGet(self.macos, find=self.find).availability().available
            )
            absent = WinGet(self.windows, find=lambda _: None)
            self.assertFalse(absent.availability().available)
            with self.assertRaises(AdapterError) as caught:
                absent.version()
            self.assertEqual(caught.exception.error.code, ErrorCode.MISSING_BACKEND)

    def test_execution_passes_exact_request_approval_and_cancellation_to_runner(self):
        runner = mock.Mock(spec=CommandRunner)
        spec = self.spec("git", "winget")
        command = self.winget.install(spec)
        approval = Approval(command, elevation=True)
        self.winget.execute(BackendAction.INSTALL, runner, spec=spec, approval=approval)
        runner.run.assert_called_once_with(
            command, approval=approval, cancel=None, observe=None
        )
