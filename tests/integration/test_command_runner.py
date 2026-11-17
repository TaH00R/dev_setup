"""Run only disposable Python children; never execute a package manager."""

import hashlib
import platform
import subprocess
import sys
import threading
import unittest
from dataclasses import replace
from unittest import mock

from models.errors import ErrorCode
from models.platform import OperatingSystem
from models.specs import PrivilegeRequirement
from system.commands import Approval, Command, Redactor
from system.identity import bind
from system.platform import normalize_os
from system.runner import CommandRunner


def fixture_command(code="print('ok')", **kwargs):
    command = Command(
        (sys.executable, "-I", "-c", code),
        "Disposable Python fixture",
        normalize_os(platform.system()),
        "test-fixture",
        **kwargs,
    )
    return bind(command) if command.requires_consent else command


class CommandRunnerTests(unittest.TestCase):
    def test_stream_hashes_binary_output_without_capture_or_observer_disclosure(self):
        data = bytes(range(256)) * 2048
        events = []
        result = CommandRunner(capture_bytes=32).run(
            fixture_command(
                "import sys;sys.stdout.buffer.write(bytes(range(256))*2048)",
                stdout_sha256_limit=len(data),
            ),
            observe=events.append,
        )
        self.assertTrue(result.succeeded)
        self.assertEqual(result.stdout_sha256, hashlib.sha256(data).hexdigest())
        self.assertEqual(result.stdout_size, len(data))
        self.assertEqual(result.stdout, "")
        self.assertFalse(result.output_truncated)
        self.assertFalse(any(event.stream == "stdout" for event in events))

    def test_stream_hash_bound_failure_and_nonzero_exit_never_expose_partial_digest(
        self,
    ):
        for code, limit in (
            ("import sys;sys.stdout.buffer.write(b'x'*1000000)", 64),
            ("import sys;print('data');sys.exit(7)", 64),
        ):
            result = CommandRunner().run(
                fixture_command(code, stdout_sha256_limit=limit)
            )
            self.assertEqual(result.error.code, ErrorCode.COMMAND_FAILED)
            self.assertIsNone(result.stdout_sha256)
            self.assertIsNone(result.stdout_size)
            self.assertEqual(result.stdout, "")

    def test_stream_hash_empty_data_and_invalid_limits(self):
        result = CommandRunner().run(fixture_command("pass", stdout_sha256_limit=0))
        self.assertTrue(result.succeeded)
        self.assertEqual(result.stdout_size, 0)
        self.assertEqual(result.stdout_sha256, hashlib.sha256(b"").hexdigest())
        for limit in (-1, True, 512 * 1024 * 1024 + 1):
            with self.assertRaises(ValueError):
                fixture_command(stdout_sha256_limit=limit)

    def test_stream_hash_timeout_and_cancellation_discard_partial_digest(self):
        code = (
            "import sys,time;sys.stdout.write('data');sys.stdout.flush();time.sleep(10)"
        )
        result = CommandRunner().run(
            fixture_command(code, stdout_sha256_limit=64, timeout_seconds=0.15)
        )
        self.assertEqual(result.error.code, ErrorCode.TIMEOUT)
        self.assertIsNone(result.stdout_sha256)
        cancel = threading.Event()
        timer = threading.Timer(0.15, cancel.set)
        timer.start()
        try:
            result = CommandRunner().run(
                fixture_command(code, stdout_sha256_limit=64), cancel=cancel
            )
        finally:
            timer.join()
        self.assertEqual(result.error.code, ErrorCode.CANCELLED)
        self.assertIsNone(result.stdout_sha256)
        self.assertEqual(result.stdout, "")

    def test_command_preview_masks_separate_credential_option_values(self):
        command = replace(
            fixture_command(),
            argv=("tool", "--token", "very-private", "--password=another-secret"),
        )
        preview = command.preview()
        self.assertNotIn("very-private", repr(preview))
        self.assertNotIn("another-secret", repr(preview))

    def test_captures_both_streams_and_structured_exit(self):
        events = []
        result = CommandRunner().run(
            fixture_command(
                "import sys;print('ok');print('dia'+'gnostic',file=sys.stderr)"
            ),
            observe=events.append,
        )
        self.assertTrue(result.succeeded)
        self.assertEqual(result.stdout.strip(), "ok")
        self.assertIn("diagnostic", result.stderr)
        self.assertEqual({event.stream for event in events}, {"stdout", "stderr"})
        self.assertNotIn("diagnostic", repr(result))

    def test_nonzero_status_is_not_success(self):
        result = CommandRunner().run(fixture_command("import sys;sys.exit(7)"))
        self.assertEqual(result.exit_code, 7)
        self.assertEqual(result.error.code, ErrorCode.COMMAND_FAILED)

    def test_native_spawn_uses_argument_array_shell_false_and_no_stdin(self):
        command = fixture_command()
        with mock.patch(
            "system.runner.subprocess.Popen", side_effect=FileNotFoundError
        ) as spawn:
            result = CommandRunner().run(command)
        self.assertEqual(result.error.code, ErrorCode.MISSING_BACKEND)
        args, kwargs = spawn.call_args
        self.assertEqual(args, (list(command.argv),))
        self.assertIs(kwargs["shell"], False)
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(kwargs["stderr"], subprocess.PIPE)

    def test_permission_and_spawn_errors_are_structured_without_exception_values(self):
        for error, code in [
            (PermissionError("secret"), ErrorCode.PERMISSION_DENIED),
            (OSError("secret"), ErrorCode.COMMAND_FAILED),
        ]:
            with (
                self.subTest(error=type(error)),
                mock.patch("system.runner.subprocess.Popen", side_effect=error),
            ):
                result = CommandRunner().run(fixture_command())
                self.assertEqual(result.error.code, code)
                self.assertNotIn("secret", result.error.message)

    def test_consent_is_bound_to_exact_command_and_elevation(self):
        command = fixture_command(mutates=True, privilege=PrivilegeRequirement.REQUIRED)
        changed = replace(command, argv=command.argv + ("different",))
        for approval in (None, Approval(command), Approval(changed, elevation=True)):
            with (
                self.subTest(approval=approval),
                mock.patch("system.runner.subprocess.Popen") as spawn,
            ):
                result = CommandRunner().run(command, approval=approval)
                self.assertEqual(result.error.code, ErrorCode.PERMISSION_DENIED)
                spawn.assert_not_called()
        self.assertTrue(
            CommandRunner()
            .run(command, approval=Approval(command, elevation=True))
            .succeeded
        )

    def test_pre_cancel_wrong_platform_and_script_shims_never_spawn(self):
        cancel = threading.Event()
        cancel.set()
        with mock.patch("system.runner.subprocess.Popen") as spawn:
            self.assertEqual(
                CommandRunner().run(fixture_command(), cancel=cancel).error.code,
                ErrorCode.CANCELLED,
            )
            other = (
                OperatingSystem.LINUX
                if platform.system() == "Windows"
                else OperatingSystem.WINDOWS
            )
            self.assertEqual(
                CommandRunner()
                .run(replace(fixture_command(), target_os=other))
                .error.code,
                ErrorCode.UNSUPPORTED_PLATFORM,
            )
            self.assertEqual(
                CommandRunner()
                .run(replace(fixture_command(), argv=("manager.cmd",)))
                .error.code,
                ErrorCode.PERMISSION_DENIED,
            )
            spawn.assert_not_called()

    def test_timeout_and_cancellation_stop_disposable_children(self):
        result = CommandRunner().run(
            fixture_command("import time;time.sleep(10)", timeout_seconds=0.1)
        )
        self.assertEqual(result.error.code, ErrorCode.TIMEOUT)
        cancel = threading.Event()
        result = CommandRunner().run(
            fixture_command("import time;print('started',flush=True);time.sleep(10)"),
            cancel=cancel,
            observe=lambda _: cancel.set(),
        )
        self.assertEqual(result.error.code, ErrorCode.CANCELLED)

    def test_capture_is_bounded_while_both_pipes_continue_draining(self):
        code = "import os;os.write(1,b'x'*100000);os.write(2,b'y'*100000)"
        result = CommandRunner(capture_bytes=1024).run(fixture_command(code))
        self.assertTrue(result.succeeded)
        self.assertEqual(len(result.stdout), 1024)
        self.assertEqual(len(result.stderr), 1024)
        self.assertTrue(result.output_truncated)

    def test_secret_split_across_reads_is_not_streamed_or_returned(self):
        events = []
        code = "import sys,time;sys.stdout.write('super');sys.stdout.flush();time.sleep(0.1);sys.stdout.write('secret')"
        result = CommandRunner(redactor=Redactor(("supersecret",))).run(
            fixture_command(code), observe=events.append
        )
        self.assertTrue(result.succeeded)
        for event in events:
            self.assertNotIn("super", event.text)
        self.assertNotIn("supersecret", result.stdout)
        self.assertIn("[REDACTED]", result.stdout)

    def test_known_credential_fields_and_custom_environment_values_are_redacted(self):
        command = fixture_command(
            "import os;print('password=veryprivate');print(os.environ['CUSTOM_VALUE'])",
            environment=(("CUSTOM_VALUE", "private-value"),),
        )
        result = CommandRunner().run(command)
        self.assertNotIn("veryprivate", result.stdout)
        self.assertNotIn("private-value", result.stdout)
        self.assertNotIn("CUSTOM_VALUE", repr(command))

    def test_observer_exception_is_isolated_from_the_actual_command_result(self):
        def broken_observer(_):
            raise RuntimeError("sensitive observer state")

        result = CommandRunner().run(
            fixture_command("print('ready',flush=True)"),
            observe=broken_observer,
        )
        self.assertTrue(result.succeeded)
        self.assertEqual(result.diagnostics[0].code.value, "observer_failed")
        self.assertNotIn("sensitive", repr(result.diagnostics))

    def test_invalid_requests_and_limits_are_rejected(self):
        for limit in (0, -1, True, 65537):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                CommandRunner(capture_bytes=limit)
        for changes in (
            dict(argv="echo command"),
            dict(timeout_seconds=float("nan")),
            dict(timeout_seconds=True),
            dict(environment=(("TOKEN", "x"), ("TOKEN", "y"))),
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(fixture_command(), **changes)
