"""Common advertised read/query contracts across every implemented backend."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from models.errors import ErrorCode
from models.platform import (
    Architecture,
    LinuxDistribution,
    LinuxFamily,
    OperatingSystem,
    Platform,
)
from models.specs import PrivilegeRequirement
from package_managers.base import AdapterError, BackendAction
from package_managers.registry import adapters_for


class AdapterContractTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "scoop.ps1").write_text("# synthetic; never executed")
        self.hosts = [
            Platform(OperatingSystem.WINDOWS, Architecture.X86_64),
            Platform(OperatingSystem.MACOS, Architecture.ARM64),
        ]
        for name, family, version in (
            ("ubuntu", LinuxFamily.DEBIAN, "24.04"),
            ("arch", LinuxFamily.ARCH, None),
            ("fedora", LinuxFamily.FEDORA, "43"),
            ("opensuse-tumbleweed", LinuxFamily.SUSE, "20261110"),
            ("alpine", LinuxFamily.ALPINE, "3.22.8"),
        ):
            self.hosts.append(
                Platform(
                    OperatingSystem.LINUX,
                    Architecture.X86_64,
                    LinuxDistribution(name, family, version=version),
                )
            )

    def test_advertised_reads_build_safe_typed_requests_without_launching_anything(
        self,
    ):
        covered = set()
        with mock.patch(
            "system.runner.subprocess.Popen",
            side_effect=AssertionError("Contract tests cannot launch native commands"),
        ) as spawn:
            for host in self.hosts:
                for adapter in adapters_for(
                    host, find=lambda name: str(self.root / name)
                ):
                    covered.add(adapter.id)
                    for action in (
                        BackendAction.VERSION,
                        BackendAction.SEARCH,
                        BackendAction.LIST,
                    ):
                        if action not in adapter.capabilities.actions:
                            continue
                        command = adapter.build(
                            action,
                            **(
                                {"query": "git"}
                                if action == BackendAction.SEARCH
                                else {}
                            ),
                        )
                        self.assertIsInstance(command.argv, tuple)
                        self.assertTrue(Path(command.argv[0]).is_absolute())
                        self.assertFalse(command.mutates)
                        self.assertEqual(command.privilege, PrivilegeRequirement.NONE)
                        self.assertEqual(command.target_os, host.os)
                        self.assertTrue(command.source_id)
                        self.assertTrue(
                            all(
                                isinstance(value, str) and "\n" not in value
                                for value in command.argv
                            )
                        )
                    for action in set(BackendAction) - adapter.capabilities.actions:
                        with self.assertRaises(AdapterError) as caught:
                            adapter.build(action)
                        self.assertEqual(
                            caught.exception.error.code, ErrorCode.UNSUPPORTED_OPERATION
                        )
            spawn.assert_not_called()
        self.assertEqual(
            covered,
            {
                "winget",
                "scoop",
                "homebrew",
                "apt",
                "pacman",
                "dnf",
                "zypper",
                "apk",
                "flatpak",
            },
        )

    def test_invalid_queries_are_rejected_before_availability_or_bootstrap(self):
        for host in self.hosts:
            for adapter in adapters_for(host, find=lambda name: str(self.root / name)):
                adapter.find = mock.Mock(
                    side_effect=AssertionError(
                        "Invalid queries cannot probe or bootstrap a manager"
                    )
                )
                for query in (
                    "--help",
                    "git;whoami",
                    "$(whoami)",
                    "git\ninstall",
                    "git|other",
                    "https://example.org/script",
                    "x" * 101,
                ):
                    with (
                        self.subTest(backend=adapter.id, query=query),
                        self.assertRaises(AdapterError),
                    ):
                        adapter.search(query)
                adapter.find.assert_not_called()

    def test_missing_backend_is_a_structured_failure_for_every_advertised_read(self):
        for host in self.hosts:
            for adapter in adapters_for(host, find=lambda _: None):
                self.assertFalse(adapter.availability().available)
                with (
                    self.subTest(backend=adapter.id),
                    self.assertRaises(AdapterError) as caught,
                ):
                    adapter.version()
                self.assertEqual(caught.exception.error.code, ErrorCode.MISSING_BACKEND)
