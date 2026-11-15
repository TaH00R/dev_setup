"""A content reference, independent of filesystem paths and process hash seed."""

import os
import subprocess
import sys
import unittest
from dataclasses import replace

from services.catalog.loader import load_catalog
from services.catalog.service import CatalogService
from tests.paths import PROJECT_ROOT


class CatalogRevisionTests(unittest.TestCase):
    def test_revision_is_content_based_and_independent_of_package_order(self):
        packages = load_catalog()
        original = CatalogService(packages).revision
        self.assertEqual(len(original), 64)
        self.assertEqual(CatalogService(tuple(reversed(packages))).revision, original)
        changed = (
            replace(packages[0], summary="Changed reviewed metadata."),
            *packages[1:],
        )
        self.assertNotEqual(CatalogService(changed).revision, original)
        spec = packages[0].install_specs[0]
        mapping_change = (
            replace(
                packages[0],
                install_specs=(
                    replace(spec, notes=(*spec.notes, "New reviewed note.")),
                    *packages[0].install_specs[1:],
                ),
            ),
            *packages[1:],
        )
        self.assertNotEqual(CatalogService(mapping_change).revision, original)

    def test_revision_matches_fresh_interpreters_with_different_hash_seeds(self):
        expected = CatalogService(load_catalog()).revision
        script = "from services.catalog.loader import load_catalog; from services.catalog.service import CatalogService; print(CatalogService(load_catalog()).revision)"
        for seed in ("1", "37"):
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=PROJECT_ROOT,
                env={**os.environ, "PYTHONHASHSEED": seed},
                capture_output=True,
                text=True,
                timeout=10,
                shell=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), expected)
