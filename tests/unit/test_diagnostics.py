"""Diagnostics export only explicit support facts, never paths or environment."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from models.platform import Architecture, OperatingSystem, Platform
from package_managers.base import Availability
from services.diagnostics import (
    collect_diagnostics,
    diagnostic_json,
    export_diagnostics,
)


class DiagnosticTests(unittest.TestCase):
    def test_export_omits_executable_locations_and_native_error_text(self):
        host = Platform(OperatingSystem.WINDOWS, Architecture.X86_64)
        backend = Availability(
            "winget",
            False,
            (("winget", "C:/private/token=secret.exe"),),
            "password=secret",
        )
        report = collect_diagnostics("0.1.0.dev0", host, (backend,))
        encoded = diagnostic_json(report)
        self.assertNotIn("secret", encoded)
        self.assertNotIn("private", encoded)
        self.assertEqual(
            json.loads(encoded)["backends"], [{"id": "winget", "available": False}]
        )

    def test_export_is_opt_in_and_does_not_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostics.json"
            report = collect_diagnostics(
                "0.1.0", Platform(OperatingSystem.LINUX, Architecture.X86_64)
            )
            export_diagnostics(report, path)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8"))["schema_version"], 1
            )
            with self.assertRaises(FileExistsError):
                export_diagnostics(report, path)

    def test_report_rejects_injected_metadata_before_export(self):
        report = collect_diagnostics(
            "0.1.0", Platform(OperatingSystem.WINDOWS, Architecture.X86_64)
        )
        for changed in (
            {"distribution_version": "token=secret"},
            {"backends": (("password=secret", True),)},
            {"app_version": "0.1.0\nsecret"},
        ):
            with self.assertRaises(ValueError):
                replace(report, **changed)
