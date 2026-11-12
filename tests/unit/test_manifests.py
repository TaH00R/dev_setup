"""Strict manifest round trips retain versions and never substitute tools."""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from models.environment import (
    EnvironmentChoice,
    Manifest,
    ManifestPackage,
    ProjectConfigChoice,
    RuntimeRequest,
)
from models.package import PackageVersion
from models.platform import Architecture, OperatingSystem, Platform
from services.catalog.service import CatalogService
from services.environments import (
    load_manifest,
    preview_manifest,
    save_manifest,
    starter_presets,
)
from services.planning import Planner


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "environment.json"

    def test_round_trip_all_typed_choices_and_overwrite_refusal(self):
        manifest = Manifest(
            "My collection",
            (ManifestPackage("python", PackageVersion("3.13.2")),),
            (RuntimeRequest("uv", "python", "3.13.2"),),
            "Personal tools",
            (ProjectConfigChoice("python", "3.13.2"),),
            (EnvironmentChoice("JAVA_HOME", "tools/java"),),
        )
        save_manifest(manifest, self.path)
        self.assertEqual(load_manifest(self.path), manifest)
        with self.assertRaises(FileExistsError):
            save_manifest(manifest, self.path)

    def test_unknown_fields_duplicate_keys_and_version_schema_refused(self):
        save_manifest(Manifest("Example", ()), self.path)
        original = self.path.read_text()
        cases = [
            original.replace('"schema_version": 1', '"schema_version": true'),
            original.replace('"name": "Example"', '"name":"one","name":"two"'),
            original.replace(
                '"packages": []', '"packages": [{"id":"git","version":"latest"}]'
            ),
            original.replace(
                '"environment": []', '"environment": [], "commands": ["whoami"]'
            ),
        ]
        for raw in cases:
            self.path.write_text(raw)
            with self.assertRaises(ValueError):
                load_manifest(self.path)

    def test_size_and_duplicate_package_limits(self):
        self.path.write_text(" " * 262145)
        with self.assertRaises(ValueError):
            load_manifest(self.path)
        with self.assertRaises(ValueError):
            Manifest("duplicate", (ManifestPackage("git"), ManifestPackage("git")))

    def test_ten_starters_use_only_reviewed_package_ids(self):
        presets = starter_presets()
        self.assertEqual(len(presets), 10)
        ids = set()
        for path in Path("catalog").rglob("*.json"):
            obj = json.loads(path.read_text())
            if "package" in obj:
                ids.add(obj["package"]["id"])
            elif "id" in obj:
                ids.add(obj["id"])
        self.assertTrue(ids)
        for preset in presets:
            self.assertTrue(all(p.package_id in ids for p in preset.packages))
        self.assertIn("not included", presets[6].description)

    def test_cross_platform_unsupported_items_are_blocking(self):
        catalog = Mock(spec=CatalogService)
        catalog.get.return_value = None
        planner = Planner(
            catalog, Platform(OperatingSystem.MACOS, Architecture.ARM64), ()
        )
        result = preview_manifest(
            Manifest("Unavailable", (ManifestPackage("missing-tool"),)), planner
        )
        self.assertFalse(result.ready)
        self.assertTrue(result.unsupported)
        self.assertEqual(result.reviewed.plan.items, ())

    def test_runtime_and_config_choices_never_execute_as_package_subset(self):
        catalog = Mock(spec=CatalogService)
        planner = Planner(
            catalog, Platform(OperatingSystem.MACOS, Architecture.ARM64), ()
        )
        manifest = Manifest(
            "Runtime",
            (),
            (RuntimeRequest("uv", "python", "3.13.2"),),
            configurations=(ProjectConfigChoice("python", "3.13.2"),),
        )
        result = preview_manifest(manifest, planner)
        self.assertFalse(result.ready)
        self.assertEqual(len(result.unsupported), 2)


if __name__ == "__main__":
    unittest.main()
