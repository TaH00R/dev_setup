"""Platform mapping and safe detection validation using synthetic package IDs."""

import unittest
from dataclasses import replace

from dev_setup.models.package import PackageSource, SourceKind, TrustLevel
from dev_setup.models.platform import Architecture as Arch
from dev_setup.models.platform import LinuxDistribution, LinuxFamily, Platform
from dev_setup.models.platform import OperatingSystem as OS
from dev_setup.models.specs import DetectionSpec, InstallSpec, PlatformSupport


class SpecificationTests(unittest.TestCase):
    def setUp(self):
        self.source = PackageSource(
            "example", "Example", SourceKind.CURATED, TrustLevel.UNKNOWN
        )
        self.support = PlatformSupport(OS.WINDOWS, (Arch.X86_64,))

    def test_linux_support_requires_exact_distribution_and_architecture(self):
        spec = PlatformSupport(OS.LINUX, (Arch.X86_64,), ("ubuntu",))
        ubuntu = Platform(
            OS.LINUX, Arch.X86_64, LinuxDistribution("ubuntu", LinuxFamily.DEBIAN)
        )
        self.assertTrue(spec.supports(ubuntu))
        self.assertFalse(spec.supports(replace(ubuntu, architecture=Arch.ARM64)))
        self.assertFalse(
            spec.supports(
                replace(
                    ubuntu, distribution=LinuxDistribution("debian", LinuxFamily.DEBIAN)
                )
            )
        )
        self.assertFalse(spec.supports(Platform(OS.LINUX, Arch.X86_64)))
        self.assertFalse(spec.supports(Platform(OS.MACOS, Arch.X86_64)))

    def test_invalid_support(self):
        for os, arches, distros in [
            (OS.UNKNOWN, (Arch.X86_64,), ()),
            (OS.LINUX, (Arch.X86_64,), ()),
            (OS.WINDOWS, (), ()),
            (OS.WINDOWS, (Arch.UNKNOWN,), ()),
            (OS.WINDOWS, (Arch.X86_64, Arch.X86_64), ()),
            (OS.MACOS, (Arch.ARM64,), ("ubuntu",)),
        ]:
            with self.subTest(os=os, arches=arches), self.assertRaises(ValueError):
                PlatformSupport(os, arches, distros)

    def test_detection_rejects_paths_scripts_and_unbounded_timeouts(self):
        for executable, args, timeout in [
            ("../tool", ("--version",), 5),
            ("tool", ("-c", "import os"), 5),
            ("tool", ("--version;command",), 5),
            ("tool", (), 5),
            ("tool", ("--version",), 0),
            ("tool", ("--version",), float("inf")),
            ("tool", ("--version",), float("nan")),
        ]:
            with (
                self.subTest(executable=executable, args=args, timeout=timeout),
                self.assertRaises(ValueError),
            ):
                DetectionSpec(executable, args, timeout)
        self.assertEqual(
            DetectionSpec("example.exe", ("-version",)).executable, "example.exe"
        )

    def test_native_ids_are_data_with_no_options_or_shell_fragments(self):
        for native_id in (
            "Vendor.Example",
            "repo/example-tool",
            "example@1.0",
            "example:amd64",
        ):
            with self.subTest(native_id=native_id):
                spec = InstallSpec(self.support, "example", native_id, self.source)
                self.assertEqual(spec.native_id, native_id)
        for native_id in (
            "--force",
            "example tool",
            "tool;command",
            "$(command)",
            "tool\n",
        ):
            with self.subTest(native_id=native_id), self.assertRaises(ValueError):
                InstallSpec(self.support, "example", native_id, self.source)

    def test_invalid_install_metadata(self):
        spec = InstallSpec(self.support, "example", "Example.Tool", self.source)
        for change in [
            dict(estimated_size_bytes=-1),
            dict(estimated_size_bytes=True),
            dict(restart_required="yes"),
            dict(privilege="admin"),
            dict(version="latest"),
            dict(notes=["note"]),
            dict(backend="--exec"),
        ]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(spec, **change)
