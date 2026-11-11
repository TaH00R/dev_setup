"""Deterministic platform records and validation."""

import unittest
from dataclasses import FrozenInstanceError
from unittest import mock

from dev_setup.models.platform import (
    Architecture,
    LinuxDistribution,
    LinuxFamily,
    OperatingSystem,
    Platform,
)
from dev_setup.system.platform import (
    detect_platform,
    distribution_from_release,
    normalize_architecture,
    normalize_os,
)


class PlatformDetectionTests(unittest.TestCase):
    def test_os_and_architecture_aliases(self):
        for alias, expected in [
            (" Windows ", OperatingSystem.WINDOWS),
            ("Darwin", OperatingSystem.MACOS),
            ("Linux", OperatingSystem.LINUX),
            ("FreeBSD", OperatingSystem.UNKNOWN),
        ]:
            with self.subTest(alias=alias):
                self.assertEqual(normalize_os(alias), expected)
        for aliases, expected in [
            (("AMD64", "x86_64", "x64"), Architecture.X86_64),
            (("aarch64", "ARM64"), Architecture.ARM64),
            (("i386", "i686", "x86"), Architecture.X86),
            (("armv7l",), Architecture.ARMV7),
            (("riscv64", ""), Architecture.UNKNOWN),
        ]:
            for alias in aliases:
                with self.subTest(alias=alias):
                    self.assertEqual(normalize_architecture(alias), expected)

    def test_distribution_families_and_exact_id_precedence(self):
        for distro, like, family in [
            ("ubuntu", "debian", LinuxFamily.DEBIAN),
            ("manjaro", "arch", LinuxFamily.ARCH),
            ("rocky", "rhel fedora", LinuxFamily.FEDORA),
            ("opensuse-tumbleweed", "suse", LinuxFamily.SUSE),
            ("alpine", "", LinuxFamily.ALPINE),
            ("example", "other debian", LinuxFamily.DEBIAN),
            ("fedora", "debian", LinuxFamily.FEDORA),
            ("example", "other", LinuxFamily.UNKNOWN),
        ]:
            with self.subTest(distro=distro):
                result = distribution_from_release({"ID": distro, "ID_LIKE": like})
                self.assertEqual(result.id, distro)
                self.assertEqual(result.family, family)

    def test_missing_and_malformed_distribution(self):
        for data in [
            {},
            {"ID": ""},
            {"ID": "bad;command"},
            {"ID": "example", "ID_LIKE": "bad;command"},
        ]:
            with self.subTest(data=data):
                self.assertIsNone(distribution_from_release(data))

    def test_release_metadata_and_deduplicated_ancestry(self):
        result = distribution_from_release(
            {
                "ID": "Example",
                "ID_LIKE": "ubuntu debian ubuntu",
                "VERSION_ID": "1.0",
                "PRETTY_NAME": "Example Linux",
            }
        )
        self.assertEqual(result.like, ("ubuntu", "debian"))
        self.assertEqual(result.version, "1.0")
        self.assertEqual(result.name, "Example Linux")

    def test_explicit_host_never_reads_local_release(self):
        with mock.patch(
            "dev_setup.system.platform.platform.freedesktop_os_release"
        ) as read:
            host = detect_platform(
                system="Linux", machine="AMD64", os_release={"ID": "ubuntu"}
            )
        read.assert_not_called()
        self.assertEqual(host.distribution.id, "ubuntu")

    def test_non_linux_never_reads_linux_metadata(self):
        with mock.patch(
            "dev_setup.system.platform.platform.freedesktop_os_release"
        ) as read:
            host = detect_platform(system="Darwin", machine="arm64")
        read.assert_not_called()
        self.assertEqual(host.os, OperatingSystem.MACOS)
        self.assertIsNone(host.distribution)

    def test_host_detection_and_unreadable_release(self):
        with (
            mock.patch(
                "dev_setup.system.platform.platform.system", return_value="Linux"
            ),
            mock.patch(
                "dev_setup.system.platform.platform.machine", return_value="aarch64"
            ),
            mock.patch(
                "dev_setup.system.platform.platform.freedesktop_os_release",
                side_effect=OSError,
            ),
        ):
            host = detect_platform()
        self.assertEqual(host.architecture, Architecture.ARM64)
        self.assertIn("could not be identified", host.unsupported_reason)


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
