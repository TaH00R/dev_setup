"""Platform mapping and safe detection validation using synthetic package IDs."""

import unittest
from dataclasses import replace
from datetime import date

from models.package import PackageSource, SourceKind, TrustLevel
from models.platform import Architecture as Arch
from models.platform import LinuxDistribution, LinuxFamily, Platform
from models.platform import OperatingSystem as OS
from models.specs import DetectionSpec, InstallSpec, MappingReview, PlatformSupport


class SpecificationTests(unittest.TestCase):
    def test_linux_release_scope_is_not_inferred_from_distribution_id(self):
        support = PlatformSupport(OS.LINUX, (Arch.X86_64,), ("ubuntu",), ("24.04",))
        for version, expected in [("24.04", True), ("22.04", False), (None, False)]:
            with self.subTest(version=version):
                host = Platform(
                    OS.LINUX,
                    Arch.X86_64,
                    LinuxDistribution("ubuntu", LinuxFamily.DEBIAN, version=version),
                )
                self.assertEqual(support.supports(host), expected)
        with self.assertRaises(ValueError):
            PlatformSupport(
                OS.WINDOWS, (Arch.X86_64,), distribution_versions=("24.04",)
            )

    def test_mapping_review_requires_https_and_an_explicit_date(self):
        review = MappingReview("https://example.org/package", date(2026, 11, 11))
        self.assertEqual(review.checked_on.isoformat(), "2026-11-11")
        with self.assertRaises(ValueError):
            MappingReview("http://example.org", date(2026, 11, 11))
        with self.assertRaises(ValueError):
            MappingReview("https://example.org", "2026-11-11")

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

    def test_version_subcommands_cannot_be_interpreter_script_names(self):
        for executable in ("python", "node", "bash", "example"):
            with self.subTest(executable=executable), self.assertRaises(ValueError):
                DetectionSpec(executable, ("version",))
        self.assertEqual(DetectionSpec("go", ("version",)).version_args, ("version",))
        for timeout in (True, "5"):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                DetectionSpec("example", timeout_seconds=timeout)
