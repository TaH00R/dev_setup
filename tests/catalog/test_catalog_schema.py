"""Synthetic catalog documents; unit tests never contact a registry."""

import copy
import unittest

from services.catalog.schema import CatalogValidationError, decode_package
from tests.fixtures.catalog import example_document


class CatalogSchemaTests(unittest.TestCase):
    def test_complete_document_decodes_to_immutable_records(self):
        document = example_document()
        package = decode_package(document)
        document["package"]["name"] = "Changed"
        self.assertEqual(package.name, "Example Tool")
        self.assertEqual(
            package.install_specs[0].review.checked_on.isoformat(), "2026-11-11"
        )
        self.assertIsInstance(package.install_specs, tuple)

    def test_unknown_executable_hooks_rejected_at_all_levels(self):
        paths = [
            (),
            ("package",),
            ("package", "install_specs", 0),
            ("package", "install_specs", 0, "source"),
            ("package", "detection", 0),
            ("package", "install_specs", 0, "support"),
            ("package", "install_specs", 0, "review"),
        ]
        for path in paths:
            with self.subTest(path=path):
                doc = example_document()
                target = doc
                for key in path:
                    target = target[key]
                target["post_install"] = "sensitive arbitrary command"
                with self.assertRaises(CatalogValidationError) as caught:
                    decode_package(doc)
                self.assertNotIn("sensitive", str(caught.exception))

    def test_invalid_shapes_types_and_schema_versions(self):
        for change in [
            {"schema_version": True},
            {"schema_version": 2},
            {"package": []},
            {"package": {}},
            {"schema_version": "1"},
        ]:
            with self.subTest(change=change), self.assertRaises(CatalogValidationError):
                decode_package(example_document() | change)
        for key, value in [
            ("tags", "tool"),
            ("name", 5),
            ("category", "unknown"),
            ("detection", [{"executable": "python", "version_args": ["-c", "code"]}]),
        ]:
            doc = example_document()
            doc["package"][key] = value
            with self.subTest(key=key), self.assertRaises(CatalogValidationError):
                decode_package(doc)

    def test_missing_review_and_incompatible_platform_backend_rejected(self):
        for change in [
            dict(backend="unknown"),
            dict(backend="apt"),
            dict(
                source={
                    "id": "x",
                    "name": "X",
                    "kind": "live",
                    "trust": "unknown",
                    "url": "https://example.org",
                }
            ),
            dict(review={"url": "http://example.org", "checked_on": "2026-11-11"}),
            dict(review={"url": "https://example.org", "checked_on": "2026-99-99"}),
            dict(restart_required=1),
            dict(estimated_size_bytes=True),
        ]:
            doc = example_document()
            doc["package"]["install_specs"][0].update(change)
            with self.subTest(change=change), self.assertRaises(CatalogValidationError):
                decode_package(doc)
        doc = example_document()
        del doc["package"]["install_specs"][0]["review"]
        with self.assertRaises(CatalogValidationError):
            decode_package(doc)

    def test_wrong_linux_family_is_rejected(self):
        doc = example_document()
        spec = doc["package"]["install_specs"][0]
        spec.update(
            backend="apt",
            support={
                "os": "linux",
                "architectures": ["x86_64"],
                "distributions": ["fedora"],
            },
        )
        with self.assertRaises(CatalogValidationError):
            decode_package(doc)

    def test_optional_fields_dependencies_and_detection_support(self):
        doc = example_document()
        doc["package"].update(
            dependencies=[
                {
                    "package_id": "example.runtime",
                    "version": {"value": "1.0", "channel": "stable"},
                }
            ],
            conflicts=["example.other"],
            documentation="https://example.org/docs",
        )
        doc["package"]["detection"][0]["support"] = copy.deepcopy(
            doc["package"]["install_specs"][0]["support"]
        )
        package = decode_package(doc)
        self.assertEqual(package.dependencies[0].version.channel, "stable")
