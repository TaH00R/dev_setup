"""Offline contract checks for every version-controlled catalog entry."""

import unittest

from catalog.loader import load_catalog
from catalog.service import CatalogService
from models.package import SourceKind
from models.platform import (
    Architecture,
    LinuxDistribution,
    LinuxFamily,
    OperatingSystem,
    Platform,
)


class ShippedCatalogTests(unittest.TestCase):
    def test_docker_products_and_database_side_effects_are_explicit(self):
        service = CatalogService(load_catalog())
        desktop = service.get("docker-desktop")
        engine = service.get("docker-engine")
        self.assertNotEqual(desktop.id, engine.id)
        self.assertEqual(
            {spec.support.os for spec in engine.install_specs}, {OperatingSystem.LINUX}
        )
        self.assertNotIn(
            OperatingSystem.LINUX, {spec.support.os for spec in desktop.install_specs}
        )
        notes = " ".join(note for spec in desktop.install_specs for note in spec.notes)
        self.assertIn("subscription", notes)
        self.assertIn("virtualization", notes)
        postgres = service.get("postgresql")
        for spec in postgres.install_specs:
            with self.subTest(backend=spec.backend):
                self.assertTrue(
                    any("data" in note or "cluster" in note for note in spec.notes)
                )

    def test_all_entries_have_reviewed_mappings_and_safe_detection(self):
        packages = load_catalog()
        self.assertTrue(packages)
        for package in packages:
            with self.subTest(package=package.id):
                self.assertTrue(package.install_specs)
                self.assertTrue(package.detection)
                self.assertTrue(package.license)
                self.assertTrue(package.vendor)
                for spec in package.install_specs:
                    self.assertIsNotNone(spec.review)
                    self.assertEqual(spec.source.kind, SourceKind.CURATED)
                    self.assertTrue(spec.source.url.startswith("https://"))
                    if spec.support.os == OperatingSystem.LINUX:
                        self.assertTrue(spec.support.distribution_versions)
                    host = Platform(
                        spec.support.os,
                        spec.support.architectures[0],
                        LinuxDistribution(
                            spec.support.distributions[0],
                            version=spec.support.distribution_versions[0],
                        )
                        if spec.support.os == OperatingSystem.LINUX
                        else None,
                    )
                    self.assertTrue(
                        any(
                            rule.support is None or rule.support.supports(host)
                            for rule in package.detection
                        )
                    )

    def test_language_and_runtime_ids_are_searchable_on_windows(self):
        service = CatalogService(load_catalog())
        target = Platform(OperatingSystem.WINDOWS, Architecture.X86_64)
        for package_id in ("python", "nodejs", "java", "gcc"):
            with self.subTest(package_id=package_id):
                matches = service.search(package_id, target=target)
                self.assertEqual(matches[0].package.id, package_id)
                self.assertTrue(matches[0].matching_specs)

    def test_sources_are_explicit_about_runtime_and_toolchain_differences(self):
        service = CatalogService(load_catalog())
        for package_id, phrase in [
            ("nodejs", "npm"),
            ("gcc", "Clang"),
            ("java", "JAVA_HOME"),
        ]:
            package = service.get(package_id)
            self.assertIn(
                phrase,
                " ".join(note for spec in package.install_specs for note in spec.notes),
            )

    def test_developer_tools_and_vscode_repository_limit(self):
        service = CatalogService(load_catalog())
        for package_id in ("git", "cmake", "vscode"):
            with self.subTest(package_id=package_id):
                self.assertIsNotNone(service.get(package_id))
        linux = Platform(
            OperatingSystem.LINUX,
            Architecture.X86_64,
            LinuxDistribution("ubuntu", LinuxFamily.DEBIAN, version="24.04"),
        )
        match = service.search("vscode", target=linux)[0]
        self.assertFalse(match.matching_specs)
        self.assertIn("No reviewed mapping", match.caveat)
        self.assertIn("Microsoft", match.package.license)
