"""Deterministic curated ranking, browsing, and target compatibility."""

import unittest
from dataclasses import replace

from models.platform import Architecture, OperatingSystem, Platform
from services.catalog.schema import decode_package
from services.catalog.service import CatalogService
from tests.fixtures.catalog import example_document


class CatalogServiceTests(unittest.TestCase):
    def setUp(self):
        self.package = decode_package(example_document())
        self.other = replace(
            self.package,
            id="example.other",
            name="Another Example",
            summary="A tool for building projects",
            category="build-tools",
            tags=("compiler",),
        )
        self.service = CatalogService((self.other, self.package))
        self.windows = Platform(OperatingSystem.WINDOWS, Architecture.X86_64)

    def test_browse_categories_and_lookup(self):
        self.assertEqual(self.service.categories, ("build-tools", "utilities"))
        self.assertEqual(len(self.service.browse(category="utilities")), 1)
        self.assertEqual(self.service.get("example.tool"), self.package)
        self.assertIsNone(self.service.get("missing"))

    def test_exact_id_ranks_first_and_casefolds_whitespace(self):
        matches = self.service.search("  EXAMPLE.TOOL  ")
        self.assertEqual(matches[0].package.id, "example.tool")
        self.assertEqual(len(self.service.search("building compiler")), 1)
        self.assertEqual(self.service.search("no-match"), ())
        self.assertEqual(
            self.service.search("example", category="utilities")[0].package,
            self.package,
        )

    def test_target_filter_retains_source_and_native_identity(self):
        match = self.service.search("Example.Tool", target=self.windows)[0]
        self.assertEqual(match.matching_specs[0].native_id, "Example.Tool")
        self.assertEqual(match.matching_specs[0].source.kind.value, "curated")
        self.assertIn("availability", match.caveat)
        self.assertIn("Choose a target", self.service.search("example")[0].caveat)

    def test_unsupported_results_remain_visible_with_reason(self):
        target = Platform(OperatingSystem.LINUX, Architecture.ARM64)
        matches = self.service.search("example", target=target)
        self.assertEqual(len(matches), 2)
        self.assertFalse(matches[0].matching_specs)
        self.assertIn("distribution", matches[0].caveat)
        self.assertEqual(
            self.service.search("example", target=target, mapped_only=True), ()
        )

    def test_ties_are_stable_across_input_order(self):
        reordered = CatalogService(tuple(reversed(self.service.packages)))
        self.assertEqual(self.service.search("example"), reordered.search("example"))

    def test_invalid_queries_and_mutable_snapshot_rejected(self):
        with self.assertRaises(ValueError):
            CatalogService([self.package])
        for kwargs in [
            dict(category="unknown"),
            dict(target="Windows"),
            dict(mapped_only="yes"),
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.service.search("example", **kwargs)
