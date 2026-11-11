"""Package model examples use synthetic IDs, not verified catalog entries."""

import unittest
from dataclasses import FrozenInstanceError

from dev_setup.models.package import (
    Dependency,
    PackageSource,
    PackageVersion,
    SourceKind,
    TrustLevel,
)


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
