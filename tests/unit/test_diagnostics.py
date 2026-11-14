"""Diagnostics export only explicit support facts, never paths or environment."""

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from models.diagnostics import DiagnosticCode, DiagnosticComponent, DiagnosticEvent
from models.errors import ErrorCode
from models.platform import Architecture, OperatingSystem, Platform
from package_managers.base import Availability
from services.diagnostics import (
    collect_diagnostics,
    diagnostic_json,
    export_diagnostics,
)
from system.diagnostics import DiagnosticBuffer


class DiagnosticTests(unittest.TestCase):
    def test_recent_registered_context_is_bounded_and_exported_only_as_reviewed_snapshot(
        self,
    ):
        buffer = DiagnosticBuffer(capacity=20)
        for index in range(30):
            buffer.record(
                DiagnosticCode.OBSERVER_FAILED,
                DiagnosticComponent.RUNNER,
                error_code=ErrorCode.UNKNOWN,
                operation_id=f"{index:032x}",
            )
        self.assertEqual(len(buffer.snapshot(128)), 20)
        with mock.patch("services.diagnostics.session_diagnostics", buffer):
            report = collect_diagnostics(
                "0.1.0", Platform(OperatingSystem.WINDOWS, Architecture.X86_64)
            )
        encoded = diagnostic_json(report)
        events = json.loads(encoded)["diagnostics"]
        self.assertEqual(len(events), 16)
        self.assertEqual(events[-1]["operation_id"], f"{29:032x}")
        self.assertEqual(
            set(events[0]),
            {"code", "component", "error_code", "operation_id", "timestamp"},
        )
        buffer.record(DiagnosticCode.STARTUP_FAILED, DiagnosticComponent.STARTUP)
        self.assertEqual(diagnostic_json(report), encoded)
        self.assertLessEqual(len(encoded.encode("utf-8")), 16384)

    def test_diagnostic_events_reject_arbitrary_codes_and_sensitive_context(self):
        for changes in (
            {"code": "token=secret"},
            {"component": "C:/private/path"},
            {"error_code": "password=secret"},
            {"operation_id": "token=secret"},
        ):
            values = {
                "code": DiagnosticCode.WORKER_FAILED,
                "component": DiagnosticComponent.WORKER,
            }
            values.update(changes)
            with self.assertRaises(ValueError):
                DiagnosticEvent(**values)
        for capacity in (0, 129, True):
            with self.assertRaises(ValueError):
                DiagnosticBuffer(capacity)

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
