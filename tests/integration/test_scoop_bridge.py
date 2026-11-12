"""Exercise the reviewed bridge using a fake Scoop script, never the real manager."""

import json
import platform
import shutil
import tempfile
import unittest
from pathlib import Path

from system.commands import Command
from system.platform import normalize_os
from system.runner import CommandRunner
from tests.paths import PROJECT_ROOT

POWERSHELL = (
    shutil.which("pwsh.exe") or shutil.which("powershell.exe") or shutil.which("pwsh")
)


@unittest.skipUnless(
    POWERSHELL, "Native PowerShell is not installed; bridge tests run on Windows CI."
)
class ScoopBridgeTests(unittest.TestCase):
    def test_search_json_projects_native_objects_without_mutation(self):
        self.script.write_text(
            "param([string] $Action, [string] $Query)\n"
            "if ($Action -ne 'search') { exit 30 }\n"
            "Write-Host 'Informational native banner'\n"
            "[PSCustomObject]@{ Name='synthetic-tool'; Source='main'; Version='1.0' }\n"
            "exit 0\n",
            encoding="utf-8",
        )
        result = self.invoke("-Action", "search-json", "-Query", "synthetic")
        self.assertTrue(result.succeeded, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data["packages"][0]["Name"], "synthetic-tool")
        self.assertFalse(data["limited"])

    def test_search_json_does_not_hide_native_failure_or_accept_command_text(self):
        self.script.write_text("exit 17\n", encoding="utf-8")
        result = self.invoke("-Action", "search-json", "-Query", "git")
        self.assertEqual(result.exit_code, 17)
        result = self.invoke("-Action", "search-json", "-Query", "git;unexpected")
        self.assertEqual(result.exit_code, 1)
        self.assertNotIn("unexpected", result.stderr)

    def test_redirected_metadata_uses_utf8(self):
        self.script.write_text(
            "[Console]::Out.WriteLine([char]0x00e9)\nexit 0\n", encoding="utf-8"
        )
        result = self.invoke("-Action", "version")
        self.assertTrue(result.succeeded, result.stderr)
        self.assertEqual(result.stdout.strip(), "é")

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.script = Path(temporary.name) / "scoop.ps1"
        self.write_fake_inventory()
        self.bridge = PROJECT_ROOT / "package_managers" / "scoop_bridge.ps1"

    def invoke(self, *arguments):
        command = Command(
            (
                POWERSHELL,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(self.bridge),
                "-ScoopScript",
                str(self.script),
                *arguments,
            ),
            "Synthetic Scoop bridge test",
            normalize_os(platform.system()),
            "test-fixture",
        )
        result = CommandRunner().run(command)
        if (
            "PSSecurityException" in result.stderr
            or "running scripts is disabled" in result.stderr
        ):
            self.skipTest(
                "Native execution policy refused -File; the bridge does not bypass it."
            )
        return result

    def write_fake_inventory(
        self, *, remote="https://github.com/ScoopInstaller/Main", installed_source=None
    ):
        apps = (
            []
            if installed_source is None
            else [{"Name": "git", "Source": installed_source, "Info": ""}]
        )
        inventory = json.dumps(
            {"buckets": [{"Name": "main", "Source": remote}], "apps": apps}
        )
        self.script.write_text(
            "param([string] $Action, [string] $Package)\n"
            "if ($Action -eq 'export') { Write-Output '" + inventory + "'; exit 0 }\n"
            "[Console]::Out.WriteLine($Action + '|' + $Package)\nexit 0\n",
            encoding="utf-8",
        )

    def test_mismatched_bucket_remote_and_installed_origin_are_refused(self):
        for remote, origin in [
            ("https://example.org/unreviewed", "main"),
            ("https://github.com/ScoopInstaller/Main", "extras"),
        ]:
            with self.subTest(remote=remote, origin=origin):
                self.write_fake_inventory(remote=remote, installed_source=origin)
                result = self.invoke("-Action", "uninstall", "-Package", "main/git")
                self.assertEqual(result.exit_code, 1)
                self.assertEqual(result.stdout, "")

    def test_routes_only_allowlisted_action_and_qualified_package(self):
        result = self.invoke("-Action", "show", "-Package", "main/git")
        self.assertTrue(result.succeeded, result.stderr)
        self.assertEqual(result.stdout.strip(), "info|main/git")

    def test_argument_injection_is_rejected_before_fake_scoop_runs(self):
        result = self.invoke("-Action", "show", "-Package", "main/git;unexpected")
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("unexpected", result.stderr)

    def test_unexpected_query_for_list_is_rejected(self):
        result = self.invoke("-Action", "list", "-Query", "git")
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stdout, "")

    def test_native_exit_status_is_preserved(self):
        self.script.write_text("exit 23\n", encoding="utf-8")
        result = self.invoke("-Action", "version")
        self.assertEqual(result.exit_code, 23)
