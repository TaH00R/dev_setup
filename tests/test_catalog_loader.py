"""Local catalog validation tests use temporary directories and synthetic IDs."""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from test_catalog_schema import example_document

from catalog.__main__ import main
from catalog.loader import (
    MAX_DOCUMENT_BYTES,
    load_catalog,
    load_package,
    validate_packages,
)
from catalog.schema import CatalogValidationError, decode_package


class CatalogLoaderTests(unittest.TestCase):
    def test_validation_command_has_useful_exit_status_and_no_package_operations(self):
        output, errors = io.StringIO(), io.StringIO()
        with (
            redirect_stdout(output),
            redirect_stderr(errors),
            mock.patch(
                "subprocess.Popen",
                side_effect=AssertionError("process execution forbidden"),
            ),
        ):
            self.assertEqual(main(["validate", "--directory", str(self.root)]), 0)
            self.assertEqual(
                main(["validate", "--directory", str(self.root / "missing")]), 1
            )
        self.assertIn("Validated 1 packages", output.getvalue())
        self.assertIn("directory does not exist", errors.getvalue())

    def test_file_errors_identify_safe_relative_path(self):
        self.path.write_text("{bad json", encoding="utf-8")
        with self.assertRaises(CatalogValidationError) as caught:
            load_catalog(self.root)
        self.assertIn("utilities/example.tool.json", str(caught.exception))
        self.assertNotIn(str(self.root), str(caught.exception))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "utilities" / "example.tool.json"
        self.path.parent.mkdir()
        self.path.write_text(json.dumps(example_document()), encoding="utf-8")

    def test_load_deterministic_valid_catalog(self):
        self.assertEqual(
            tuple(p.id for p in load_catalog(self.root)), ("example.tool",)
        )

    def test_duplicate_keys_malformed_encoding_and_size_rejected(self):
        for raw in [
            b'{"schema_version":1,"schema_version":1}',
            b"{",
            b"\xff",
            b" " * (MAX_DOCUMENT_BYTES + 1),
            b'{"value":NaN}',
            b"[" * 1500 + b"]" * 1500,
        ]:
            with (
                self.subTest(length=len(raw)),
                self.assertRaises(CatalogValidationError),
            ):
                self.path.write_bytes(raw)
                load_package(self.path)

    def test_duplicate_id_and_wrong_path_are_rejected(self):
        package = decode_package(example_document())
        with self.assertRaises(CatalogValidationError):
            validate_packages((package, package))
        self.path.rename(self.path.with_name("wrong.json"))
        with self.assertRaises(CatalogValidationError):
            load_catalog(self.root)

    def test_empty_missing_and_wrong_depth_fail_closed(self):
        self.path.unlink()
        with self.assertRaises(CatalogValidationError):
            load_catalog(self.root)
        with self.assertRaises(CatalogValidationError):
            load_catalog(self.root / "absent")
        (self.root / "example.tool.json").write_text(
            json.dumps(example_document()), encoding="utf-8"
        )
        with self.assertRaises(CatalogValidationError):
            load_catalog(self.root)

    def test_unresolved_references_inconsistent_sources_and_duplicate_mappings(self):
        for mutation in ("reference", "source", "mapping"):
            doc = example_document()
            if mutation == "reference":
                doc["package"]["dependencies"] = [{"package_id": "example.missing"}]
                packages = (decode_package(doc),)
            elif mutation == "source":
                other = example_document()
                other["package"]["id"] = "example.other"
                other["package"]["install_specs"][0]["source"]["name"] = (
                    "Different identity"
                )
                packages = (decode_package(doc), decode_package(other))
            else:
                import copy

                extra = copy.deepcopy(doc["package"]["install_specs"][0])
                extra["notes"] = ["Another note does not make another native mapping."]
                doc["package"]["install_specs"].append(extra)
                packages = (decode_package(doc),)
            with (
                self.subTest(mutation=mutation),
                self.assertRaises(CatalogValidationError),
            ):
                validate_packages(packages)
