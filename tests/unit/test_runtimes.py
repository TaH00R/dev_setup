"""Native runtime plans are inert until exact consent and verify native inventory."""

import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from unittest.mock import Mock, patch

from models.environment import RuntimeConsent, RuntimeRequest
from models.errors import ErrorCode, OperationError
from models.operations import OperationState
from models.planning import utc_now
from models.platform import Architecture, OperatingSystem, Platform
from package_managers.base import AdapterError, BackendAction
from runtimes.adapters import Mise, Rustup, Uv
from runtimes.service import RuntimeService
from system.commands import CommandResult


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.host = Platform(OperatingSystem.WINDOWS, Architecture.X86_64)
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / "uv.exe")
        self.adapter = Uv(self.host, find=lambda _: self.path)
        self.request = RuntimeRequest("uv", "python", "3.13.2")
        self.runner = Mock()
        self.service = RuntimeService(self.host, self.runner, (self.adapter,))

    def result(self, output="[]", *, code=None, truncated=False):
        command = self.adapter.inventory(self.request)
        return CommandResult(
            command.preview(),
            utc_now(),
            utc_now(),
            1 if code else 0,
            output,
            output_truncated=truncated,
            error=OperationError(code, "Native command failed.") if code else None,
        )

    def test_exact_uv_commands_and_capabilities(self):
        self.assertEqual(
            self.adapter.install(self.request).argv,
            (
                self.path,
                "--no-config",
                "--no-progress",
                "python",
                "install",
                "--no-bin",
                "--",
                "3.13.2",
            ),
        )
        self.assertEqual(
            self.adapter.inventory(self.request).argv,
            (
                self.path,
                "--no-config",
                "--no-progress",
                "--offline",
                "python",
                "list",
                "--only-installed",
                "--managed-python",
                "--output-format",
                "json",
            ),
        )
        self.assertNotIn(BackendAction.UNINSTALL, self.adapter.capabilities.actions)

    def test_other_managers_exact_commands(self):
        mise = Mise(self.host, find=lambda _: self.path)
        request = RuntimeRequest("mise", "node", "22.14.0")
        self.assertEqual(
            mise.install(request).argv[1:],
            ("--no-config", "--no-env", "--no-hooks", "install", "--", "node@22.14.0"),
        )
        rust = Rustup(self.host, find=lambda _: self.path)
        self.assertEqual(
            rust.install(RuntimeRequest("rustup", "rust", "1.85.0")).argv[1:],
            (
                "toolchain",
                "install",
                "1.85.0-x86_64-pc-windows-msvc",
                "--profile",
                "minimal",
                "--no-self-update",
            ),
        )
        self.assertEqual(
            rust.parse_inventory(
                "1.85.0-x86_64-pc-windows-msvc (default)\nstable-x86_64-pc-windows-msvc (active)\n"
            ),
            ("1.85.0",),
        )

    def test_missing_or_script_manager_refused(self):
        for found in (None, "uv.exe", str(Path(self.directory.name) / "uv.cmd")):
            adapter = Uv(self.host, find=lambda _, value=found: value)
            self.assertFalse(adapter.availability().available)
            with self.assertRaises(AdapterError):
                adapter.install(self.request)

    def test_alias_option_and_path_versions_refused(self):
        for version in (
            "latest",
            "stable",
            "--help",
            "../3.13.2",
            "3.13.2;whoami",
            "3.13.2\n",
        ):
            with self.assertRaises(ValueError):
                RuntimeRequest("uv", "python", version)

    def test_preview_only_reads_inventory_and_consent_is_exact(self):
        self.runner.run.return_value = self.result()
        plan = self.service.preview(self.request)
        self.assertFalse(self.runner.run.call_args.args[0].mutates)
        self.assertEqual(self.service.execute(plan).state, OperationState.CANCELLED)
        self.assertEqual(
            self.service.execute(
                plan, RuntimeConsent(replace(plan, id="another"), True)
            ).state,
            OperationState.CANCELLED,
        )
        self.assertEqual(self.runner.run.call_count, 1)

    def test_unapproved_or_mismatched_consent_never_creates_audit(self):
        self.runner.run.return_value = self.result()
        plan = self.service.preview(self.request)
        for consent in (
            None,
            RuntimeConsent(plan, False),
            RuntimeConsent(replace(plan, id="other-plan"), True),
        ):
            result = self.service.execute(plan, consent)
            self.assertIsNone(result.audit)
        self.assertEqual(self.runner.run.call_count, 1)

    def test_success_requires_inventory_verification_and_bound_approval(self):
        installed = json.dumps(
            [
                {
                    "key": "cpython-3.13.2-windows-x86_64-none",
                    "path": "runtime/python.exe",
                }
            ]
        )
        self.runner.run.side_effect = [
            self.result(),
            self.result(),
            self.result(),
            self.result(installed),
        ]
        plan = self.service.preview(self.request)
        cancel = Event()
        result = self.service.execute(plan, RuntimeConsent(plan, True), cancel=cancel)
        self.assertEqual(result.state, OperationState.SUCCEEDED)
        mutation = self.runner.run.call_args_list[2]
        self.assertTrue(mutation.args[0].mutates)
        self.assertTrue(mutation.kwargs["approval"].permits(plan.command))
        self.assertIs(mutation.kwargs["cancel"], cancel)
        self.assertEqual(result.audit.manager, "uv")
        self.assertEqual(result.audit.runtime, "python")
        self.assertEqual(result.audit.version, "3.13.2")
        self.assertEqual(result.audit.plan_id, plan.id)
        self.assertEqual(result.audit.state, OperationState.SUCCEEDED)
        self.assertEqual(result.audit.exit_code, 0)

    def test_zero_exit_without_matching_version_is_failure(self):
        command_result = self.result()
        self.runner.run.return_value = command_result
        plan = self.service.preview(self.request)
        consent = RuntimeConsent(plan, True)
        result = self.service.execute(plan, consent)
        self.assertEqual(result.error.code, ErrorCode.VERIFICATION_FAILED)
        self.assertIs(result.command_result, command_result)
        self.assertEqual(result.command_result.exit_code, 0)
        self.assertEqual(result.audit.state, OperationState.FAILED)
        self.assertEqual(result.audit.approved_at, consent.approved_at)
        self.assertGreaterEqual(result.audit.finished_at, result.audit.approved_at)
        self.assertEqual(result.audit.exit_code, 0)
        self.assertNotIn("stdout", repr(result.audit))

    def test_unexpected_verification_exception_retains_command_result_without_leaking(
        self,
    ):
        command_result = self.result("retained native output")
        self.runner.run.side_effect = [
            self.result(),
            self.result(),
            command_result,
            RuntimeError("token=do-not-display"),
        ]
        plan = self.service.preview(self.request)
        result = self.service.execute(plan, RuntimeConsent(plan, True))
        self.assertEqual(result.error.code, ErrorCode.UNKNOWN)
        self.assertEqual(result.state, OperationState.FAILED)
        self.assertIs(result.command_result, command_result)
        self.assertNotIn("do-not-display", result.message)

    def test_already_installed_is_verified_skip(self):
        self.runner.run.return_value = self.result(
            '[{"key":"cpython-3.13.2-windows-x86_64-none","path":"python.exe"}]'
        )
        plan = self.service.preview(self.request)
        self.assertIsNone(plan.command)
        self.assertEqual(
            self.service.execute(plan, RuntimeConsent(plan, True)).state,
            OperationState.SKIPPED,
        )
        self.assertTrue(
            all(not call.args[0].mutates for call in self.runner.run.call_args_list)
        )

    def test_approved_skips_and_preinstallation_failures_have_unique_audit(self):
        self.runner.run.return_value = self.result()
        plan = self.service.preview(self.request)
        # A changed reviewed command is refused after exact consent and still audited.
        with patch.object(
            self.adapter,
            "install",
            side_effect=AdapterError(
                ErrorCode.MISSING_BACKEND, "Manager is no longer available."
            ),
        ):
            first = self.service.execute(plan, RuntimeConsent(plan, True))
            second = self.service.execute(plan, RuntimeConsent(plan, True))
        self.assertEqual(first.audit.state, OperationState.FAILED)
        self.assertIsNone(first.audit.exit_code)
        self.assertNotEqual(first.audit.operation_id, second.audit.operation_id)

    def test_inventory_changes_require_new_review(self):
        self.runner.run.side_effect = [
            self.result(),
            self.result(
                '[{"key":"cpython-3.12.9-windows-x86_64-none","path":"python.exe"}]'
            ),
        ]
        plan = self.service.preview(self.request)
        self.assertEqual(
            self.service.execute(plan, RuntimeConsent(plan, True)).state,
            OperationState.FAILED,
        )
        self.assertEqual(self.runner.run.call_count, 2)

    def test_inventory_failure_timeout_cancel_and_truncation(self):
        for result in (
            self.result(code=ErrorCode.NETWORK),
            self.result(code=ErrorCode.TIMEOUT),
            self.result(code=ErrorCode.CANCELLED),
            self.result(truncated=True),
            self.result("not json"),
        ):
            self.runner.run.return_value = result
            with self.assertRaises(AdapterError):
                self.service.preview(self.request)

    def test_native_mutation_failure_is_structured(self):
        for code in (ErrorCode.COMMAND_FAILED, ErrorCode.CANCELLED, ErrorCode.TIMEOUT):
            self.runner.run.side_effect = [
                self.result(),
                self.result(),
                self.result(code=code),
            ]
            plan = self.service.preview(self.request)
            result = self.service.execute(plan, RuntimeConsent(plan, True))
            self.assertEqual(result.error.code, code)

    def test_unsafe_uv_origin_overrides_refused(self):
        with patch.dict("os.environ", {"UV_INSECURE_HOST": "example.org"}):
            with self.assertRaises(AdapterError):
                self.service.preview(self.request)

    def test_staged_manager_is_explicitly_unsupported(self):
        with self.assertRaises(AdapterError):
            self.service.preview(RuntimeRequest("sdkman", "java", "21.0.1"))

    def test_unsupported_host_is_structured_before_native_arguments(self):
        adapter = Rustup(
            Platform(OperatingSystem.UNKNOWN, Architecture.X86_64),
            find=lambda _: self.path,
        )
        with self.assertRaises(AdapterError) as error:
            adapter.install(RuntimeRequest("rustup", "rust", "1.85.0"))
        self.assertEqual(error.exception.error.code, ErrorCode.UNSUPPORTED_PLATFORM)

    def test_native_inventory_rejects_wrong_architecture_and_broken_runtime(self):
        self.assertEqual(
            self.adapter.parse_inventory(
                '[{"key":"cpython-3.13.2-windows-aarch64-none","path":"python.exe"}]'
            ),
            (),
        )
        mise = Mise(self.host, find=lambda _: self.path)
        self.assertEqual(
            mise.parse_inventory(
                '[{"version":"3.13.2","installed":true,"broken":true}]'
            ),
            (),
        )
        with self.assertRaises(AdapterError):
            mise.parse_inventory('[{"version":"3.13.2","installed":"true"}]')

    def test_mise_explicit_tool_inventory_uses_native_array_shape(self):
        # Upstream ls.rs display_json emits Vec<JSONToolVersion> when the positional
        # installed_tool is present; the keyed JSONOutput map is for no-tool queries.
        mise = Mise(self.host, find=lambda _: self.path)
        request = RuntimeRequest("mise", "python", "3.13.2")
        self.assertEqual(
            mise.inventory(request).argv[1:],
            (
                "--no-config",
                "--no-env",
                "--no-hooks",
                "ls",
                "--installed",
                "--json",
                "--",
                "python",
            ),
        )
        row = {
            "version": "3.13.2",
            "install_path": "managed/python/3.13.2",
            "installed": True,
            "active": False,
        }
        self.assertEqual(mise.parse_inventory(json.dumps([row])), ("3.13.2",))
        with self.assertRaises(AdapterError):
            mise.parse_inventory(json.dumps({"python": [row]}))


if __name__ == "__main__":
    unittest.main()
