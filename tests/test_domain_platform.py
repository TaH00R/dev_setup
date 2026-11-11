"""Deterministic platform records and validation."""

import unittest
from dataclasses import FrozenInstanceError

from dev_setup.models.platform import (
    Architecture,
    LinuxDistribution,
    LinuxFamily,
    OperatingSystem,
    Platform,
)


class PlatformModelTests(unittest.TestCase):
    def test_known_platform_and_frozen_identity(self):
        host = Platform(OperatingSystem.WINDOWS, Architecture.X86_64)
        self.assertIsNone(host.unsupported_reason)
        with self.assertRaises(FrozenInstanceError):
            host.os = OperatingSystem.LINUX

    def test_unknown_inputs_explain_support(self):
        for host, reason in [
            (
                Platform(OperatingSystem.UNKNOWN, Architecture.X86_64),
                "operating system",
            ),
            (Platform(OperatingSystem.LINUX, Architecture.UNKNOWN), "architecture"),
            (
                Platform(OperatingSystem.LINUX, Architecture.ARM64),
                "could not be identified",
            ),
            (
                Platform(
                    OperatingSystem.LINUX,
                    Architecture.X86,
                    LinuxDistribution("example"),
                ),
                "no known family",
            ),
        ]:
            with self.subTest(host=host):
                self.assertIn(reason, host.unsupported_reason)

    def test_invalid_distribution_identity(self):
        for kwargs in [
            {"id": "Bad distro"},
            {"id": "linux", "like": ["debian"]},
            {"id": "linux", "like": ("debian", "debian")},
            {"id": "linux", "version": ""},
            {"id": "linux", "family": "debian"},
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                LinuxDistribution(**kwargs)

    def test_platform_rejects_string_enums_and_misplaced_distro(self):
        with self.assertRaises(ValueError):
            Platform("windows", Architecture.X86_64)
        with self.assertRaises(ValueError):
            Platform(OperatingSystem.WINDOWS, "amd64")
        with self.assertRaises(ValueError):
            Platform(
                OperatingSystem.MACOS,
                Architecture.ARM64,
                LinuxDistribution("debian", LinuxFamily.DEBIAN),
            )
