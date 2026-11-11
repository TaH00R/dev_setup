"""Exact Linux native commands with injected paths; no manager is executed."""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from models.errors import ErrorCode, OperationError
from models.package import PackageVersion
from models.planning import utc_now
from models.platform import (
    Architecture,
    LinuxDistribution,
    LinuxFamily,
    OperatingSystem,
    Platform,
)
from models.specs import PrivilegeRequirement
from package_managers.base import AdapterError
from package_managers.base import BackendAction as A
from package_managers.flatpak import Flatpak
from package_managers.linux import APK, DNF, Pacman, Zypper
from package_managers.registry import adapters_for
from services.catalog.loader import load_catalog
from system.commands import Approval, CommandResult
from system.runner import CommandRunner


class LinuxAdapterTests(unittest.TestCase):
    def test_flatpak_rejects_unreviewed_ids_sources_versions_and_elevation(self):
        manager, spec = self.flatpak(), self.selected("flatpak")
        for changes in [
            dict(native_id="code"),
            dict(native_id="app/com.visualstudio.code/x86_64/stable"),
            dict(source=replace(spec.source, id="other")),
            dict(version=PackageVersion("1.0")),
            dict(privilege=PrivilegeRequirement.NATIVE_PROMPT),
        ]:
            with self.subTest(changes=changes), self.assertRaises(AdapterError):
                manager.install(replace(spec, **changes))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.find = lambda name: str(self.root / name)
        self.packages = {package.id: package for package in load_catalog()}

    def host(self, distro, family, version=None):
        return Platform(
            OperatingSystem.LINUX,
            Architecture.X86_64,
            LinuxDistribution(distro, family, version=version),
        )

    def selected(self, backend):
        return next(
            spec
            for spec in self.packages[
                "vscode" if backend == "flatpak" else "git"
            ].install_specs
            if spec.backend == backend
        )

    def managers(self):
        return (
            Pacman(self.host("arch", LinuxFamily.ARCH), find=self.find),
            DNF(self.host("fedora", LinuxFamily.FEDORA, "43"), find=self.find),
            Zypper(
                self.host("opensuse-tumbleweed", LinuxFamily.SUSE, "20261110"),
                find=self.find,
            ),
            APK(self.host("alpine", LinuxFamily.ALPINE, "3.22.8"), find=self.find),
        )

    def test_exact_native_commands_for_each_supported_action(self):
        expected = {
            "pacman": {
                A.VERSION: ("--version",),
                A.SEARCH: ("-Ss", "--", "git"),
                A.LIST: ("-Q",),
                A.SHOW: ("-Si", "--", "extra/git"),
                A.INSTALL: ("-S", "--needed", "--noconfirm", "--", "extra/git"),
                A.UNINSTALL: ("-R", "--noconfirm", "--", "git"),
            },
            "dnf": {
                A.VERSION: ("--version",),
                A.SEARCH: ("--cacheonly", "search", "--", "git"),
                A.LIST: ("--cacheonly", "list", "--installed"),
                A.SHOW: (
                    "--cacheonly",
                    "--repo=fedora",
                    "--repo=updates",
                    "info",
                    "--",
                    "git",
                ),
                **{
                    action: (
                        "--assumeyes",
                        "--repo=fedora",
                        "--repo=updates",
                        "--setopt=clean_requirements_on_remove=False",
                        verb,
                        "--",
                        "git",
                    )
                    for action, verb in [
                        (A.INSTALL, "install"),
                        (A.UNINSTALL, "remove"),
                        (A.UPGRADE, "upgrade"),
                    ]
                },
            },
            "zypper": {
                A.VERSION: ("--version",),
                A.SEARCH: (
                    "--non-interactive",
                    "--no-refresh",
                    "search",
                    "--type",
                    "package",
                    "--",
                    "git",
                ),
                A.LIST: (
                    "--non-interactive",
                    "--no-refresh",
                    "search",
                    "--installed-only",
                    "--type",
                    "package",
                ),
                **{
                    action: (
                        "--non-interactive",
                        "--no-refresh",
                        verb,
                        "--type",
                        "package",
                    )
                    + (() if action == A.UNINSTALL else ("--repo", "repo-oss"))
                    + ("--", "git-core")
                    for action, verb in [
                        (A.INSTALL, "install"),
                        (A.UNINSTALL, "remove"),
                        (A.UPGRADE, "update"),
                        (A.SHOW, "info"),
                    ]
                },
            },
            "apk": {
                A.VERSION: ("--version",),
                A.SEARCH: ("--no-network", "search", "--", "git"),
                A.LIST: ("info", "--verbose"),
                **{
                    action: (("--no-network",) if action == A.SHOW else ())
                    + (
                        "--repositories-file",
                        "/dev/null",
                        "--repository",
                        APK.source_url,
                        verb,
                        "--",
                        "git",
                    )
                    for action, verb in [
                        (A.INSTALL, "add"),
                        (A.UNINSTALL, "del"),
                        (A.SHOW, "policy"),
                    ]
                },
            },
        }
        with (
            mock.patch(
                "package_managers.linux.os.geteuid", return_value=1000, create=True
            ),
            mock.patch(
                "subprocess.Popen", side_effect=AssertionError("unexpected process")
            ),
        ):
            for manager in self.managers():
                for action, arguments in expected[manager.id].items():
                    with self.subTest(backend=manager.id, action=action):
                        spec = (
                            self.selected(manager.id)
                            if action in {A.INSTALL, A.UNINSTALL, A.UPGRADE, A.SHOW}
                            else None
                        )
                        command = manager.build(
                            action,
                            spec=spec,
                            query="git" if action == A.SEARCH else None,
                        )
                        prefix = (
                            (self.find("pkexec"), "--disable-internal-agent")
                            if action in {A.INSTALL, A.UNINSTALL, A.UPGRADE}
                            else ()
                        )
                        self.assertEqual(
                            command.argv, prefix + (self.find(manager.id),) + arguments
                        )
                        self.assertEqual(
                            command.requires_consent,
                            action in {A.INSTALL, A.UNINSTALL, A.UPGRADE},
                        )

    def test_registry_selects_only_matching_system_manager_plus_flatpak(self):
        for manager in self.managers():
            with self.subTest(backend=manager.id):
                self.assertEqual(
                    tuple(
                        item.id for item in adapters_for(manager.host, find=self.find)
                    ),
                    (manager.id, "flatpak"),
                )

    def test_unsupported_upgrade_versions_bad_sources_and_local_archives_fail_closed(
        self,
    ):
        for manager in self.managers():
            spec = self.selected(manager.id)
            for native in ("local.rpm", "local.apk", "repo/package", "group@name"):
                with (
                    self.subTest(backend=manager.id, native=native),
                    self.assertRaises(AdapterError),
                ):
                    manager.show(replace(spec, native_id=native))
            with self.assertRaises(AdapterError):
                manager.show(
                    replace(
                        spec,
                        source=replace(
                            spec.source, url="https://example.org/unreviewed"
                        ),
                    )
                )
            with self.assertRaises(AdapterError):
                manager.search("--help")
            with self.assertRaises(AdapterError):
                manager.show(replace(spec, version=PackageVersion("1.0")))
            if manager.id in {"pacman", "apk"}:
                with self.assertRaises(AdapterError) as caught:
                    manager.upgrade(spec)
                self.assertEqual(
                    caught.exception.error.code, ErrorCode.UNSUPPORTED_OPERATION
                )

    def test_missing_backend_or_elevation_is_explicit_and_preserves_root_execution(
        self,
    ):
        host = self.host("fedora", LinuxFamily.FEDORA, "43")
        with self.assertRaises(AdapterError):
            DNF(host, find=lambda _: None).version()
        manager = DNF(
            host, find=lambda name: None if name == "pkexec" else self.find(name)
        )
        with (
            mock.patch(
                "package_managers.linux.os.geteuid", return_value=1000, create=True
            ),
            self.assertRaises(AdapterError) as caught,
        ):
            manager.install(self.selected("dnf"))
        self.assertEqual(caught.exception.error.code, ErrorCode.PERMISSION_DENIED)
        with mock.patch(
            "package_managers.linux.os.geteuid", return_value=0, create=True
        ):
            self.assertEqual(
                manager.install(self.selected("dnf")).argv[0], self.find("dnf")
            )

    def flatpak(self):
        return Flatpak(self.host("ubuntu", LinuxFamily.DEBIAN, "24.04"), find=self.find)

    def result(self, command, stdout="", error=None, truncated=False):
        return CommandResult(
            command.preview(),
            utc_now(),
            utc_now(),
            exit_code=0 if error is None else 1,
            stdout=stdout,
            error=error,
            output_truncated=truncated,
        )

    def test_flatpak_exact_architecture_branch_user_scope_and_native_metadata(self):
        manager, spec = self.flatpak(), self.selected("flatpak")
        reference = "app/com.visualstudio.code/x86_64/stable"
        for action, args in [
            (
                A.INSTALL,
                ("install", "--user", "--noninteractive", "flathub", reference),
            ),
            (A.UNINSTALL, ("uninstall", "--user", "--noninteractive", reference)),
            (A.UPGRADE, ("update", "--user", "--noninteractive", reference)),
            (A.SHOW, ("remote-info", "--user", "flathub", reference)),
        ]:
            with self.subTest(action=action):
                self.assertEqual(
                    manager.build(action, spec=spec).argv,
                    (self.find("flatpak"),) + args,
                )
        self.assertEqual(
            manager.list_installed().argv,
            (
                self.find("flatpak"),
                "list",
                "--user",
                "--app",
                "--columns=application,version,origin,branch,arch",
            ),
        )
        self.assertEqual(
            manager.search("code").argv,
            (
                self.find("flatpak"),
                "search",
                "--user",
                "--columns=application,name,description,remotes",
                "--",
                "code",
            ),
        )

    def test_flatpak_verifies_remote_and_installed_origin_before_mutations(self):
        manager, spec = self.flatpak(), self.selected("flatpak")
        command = manager.uninstall(spec)
        for remote, origin, truncated, expected_calls in [
            ("flathub\thttps://example.org/repo", "flathub", False, 1),
            ("flathub\thttps://dl.flathub.org/repo/", "other", False, 2),
            ("flathub\thttps://dl.flathub.org/repo/", "flathub", True, 1),
            ("flathub\thttps://dl.flathub.org/repo/", "flathub", False, 3),
        ]:
            runner = mock.Mock(spec=CommandRunner)
            runner.run.side_effect = [
                self.result(command, remote, truncated=truncated),
                self.result(command, origin),
                self.result(command),
            ]
            result = manager.execute(
                A.UNINSTALL, runner, spec=spec, approval=Approval(command)
            )
            self.assertEqual(runner.run.call_count, expected_calls)
            self.assertEqual(result.succeeded, expected_calls == 3)

    def test_native_backend_errors_propagate_and_bad_releases_do_not_match(self):
        manager, spec = self.flatpak(), self.selected("flatpak")
        command = manager.install(spec)
        error = OperationError(ErrorCode.NETWORK, "Network unavailable.")
        runner = mock.Mock(spec=CommandRunner)
        runner.run.return_value = self.result(command, error=error)
        self.assertEqual(
            manager.execute(
                A.INSTALL, runner, spec=spec, approval=Approval(command)
            ).error,
            error,
        )
        for distro, family, version, kind in [
            ("manjaro", LinuxFamily.ARCH, "26", Pacman),
            ("fedora", LinuxFamily.FEDORA, "44", DNF),
            ("alpine", LinuxFamily.ALPINE, "3.23.0", APK),
        ]:
            with self.subTest(distro=distro), self.assertRaises(AdapterError):
                kind(self.host(distro, family, version), find=self.find).show(
                    self.selected(kind.id)
                )
