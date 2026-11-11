"""Package model examples use synthetic IDs, not verified catalog entries."""

import unittest
from dataclasses import FrozenInstanceError, replace

from dev_setup.models.catalog import Package
from dev_setup.models.package import (
    Dependency,
    PackageSource,
    PackageVersion,
    SourceKind,
    TrustLevel,
)
from dev_setup.models.platform import Architecture, OperatingSystem
from dev_setup.models.specs import DetectionSpec, InstallSpec, PlatformSupport


class PackageAggregateTests(unittest.TestCase):
    def setUp(self):
        self.source = PackageSource(
            "example", "Example", SourceKind.CURATED, TrustLevel.UNKNOWN
        )
        self.spec = InstallSpec(
            PlatformSupport(OperatingSystem.WINDOWS, (Architecture.X86_64,)),
            "example",
            "Example.Tool",
            self.source,
        )
        self.package = Package(
            "example.tool",
            "Example tool",
            "Synthetic model test",
            "utilities",
            "https://example.org",
            (self.spec,),
            (DetectionSpec("example"),),
        )

    def test_complete_declarative_package(self):
        package = replace(
            self.package,
            documentation="https://example.org/docs",
            license="MIT",
            vendor="Example",
            tags=("example",),
            dependencies=(Dependency("example.runtime"),),
            conflicts=("example.other",),
            environment_guidance=("Review PATH before making changes.",),
        )
        self.assertEqual(package.install_specs[0].source, self.source)
        self.assertEqual(package.dependencies[0].package_id, "example.runtime")

    def test_unavailable_package_can_have_no_mapping(self):
        package = replace(self.package, install_specs=())
        self.assertFalse(package.install_specs)

    def test_rejects_unsafe_links_invalid_records_and_duplicate_metadata(self):
        for change in [
            dict(homepage="javascript:alert(1)"),
            dict(documentation="http://example.org"),
            dict(install_specs=[self.spec]),
            dict(install_specs=(self.spec, self.spec)),
            dict(detection=("example --version",)),
            dict(tags=("tool", "tool")),
            dict(dependencies=("example.runtime",)),
            dict(conflicts=("bad id",)),
            dict(environment_guidance=("",)),
            dict(vendor=""),
        ]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(self.package, **change)

    def test_self_reference_dependency_duplicates_and_contradictions(self):
        for change in [
            dict(dependencies=(Dependency(self.package.id),)),
            dict(conflicts=(self.package.id,)),
            dict(
                dependencies=(
                    Dependency("example.runtime"),
                    Dependency("example.runtime", PackageVersion("2")),
                )
            ),
            dict(
                dependencies=(Dependency("example.runtime"),),
                conflicts=("example.runtime",),
            ),
        ]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(self.package, **change)


class PackageIdentityTests(unittest.TestCase):
    def test_provenance_is_explicit_and_frozen(self):
        curated = PackageSource(
            "example",
            "Example source",
            SourceKind.CURATED,
            TrustLevel.COMMUNITY_REPOSITORY,
            "https://example.org/packages",
        )
        live = PackageSource(
            "example",
            "Example source",
            SourceKind.LIVE,
            TrustLevel.COMMUNITY_REPOSITORY,
        )
        self.assertNotEqual(curated, live)
        self.assertEqual(curated.trust, TrustLevel.COMMUNITY_REPOSITORY)
        with self.assertRaises(FrozenInstanceError):
            curated.name = "changed"

    def test_invalid_sources(self):
        valid = dict(
            id="example",
            name="Example",
            kind=SourceKind.CURATED,
            trust=TrustLevel.UNKNOWN,
        )
        for change in [
            dict(id="--install"),
            dict(name=" "),
            dict(kind="curated"),
            dict(trust="official"),
            dict(url="http://example.org"),
            dict(url="https://user:password@example.org"),
            dict(url="https://example.org:bad"),
            dict(url="https://"),
            dict(url="https://example.org/a b"),
        ]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                PackageSource(**(valid | change))

    def test_versions_preserve_native_syntax_without_semver_assumptions(self):
        for value in ("1:2.3-4ubuntu1", "2026.1", "1.0.0-beta+build"):
            with self.subTest(value=value):
                self.assertEqual(PackageVersion(value, "stable").value, value)
        for value in ("", " 1.0", "1.0\nsecret"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                PackageVersion(value)
        with self.assertRaises(ValueError):
            PackageVersion("1.0", "")

    def test_dependencies_use_stable_ids_and_typed_versions(self):
        self.assertEqual(
            Dependency("example.tool", PackageVersion("1.0")).package_id, "example.tool"
        )
        for value in ("Example Tool", "../tool", "tool;command", "tool\x00"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Dependency(value)
        with self.assertRaises(ValueError):
            Dependency("example", "1.0")
