"""Stress and ownership failures use only disposable Python process trees."""

import ctypes
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from models.diagnostics import DiagnosticCode
from models.errors import ErrorCode
from system.coordination import NativeCoordinator
from system.runner import CommandRunner
from tests.integration.test_command_runner import fixture_command as _fixture_command


def fixture_command(code, **kwargs):
    return _fixture_command(f"exec({code!r})", **kwargs)


def alive(pid):
    if sys.platform == "win32":
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        api.OpenProcess.restype = ctypes.c_void_p
        api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        api.WaitForSingleObject.restype = ctypes.c_ulong
        api.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = api.OpenProcess(0x100000, False, pid)
        if not handle:
            return False
        try:
            return api.WaitForSingleObject(handle, 0) == 258
        finally:
            api.CloseHandle(handle)
    status = Path(f"/proc/{pid}/stat")
    if status.exists() and status.read_text().split(")", 1)[1].strip().startswith("Z"):
        return False  # A reparented zombie is no longer executing.
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class ProcessFailureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.runner = CommandRunner(coordinator=NativeCoordinator(self.root / "locks"))
        self.spawned = []
        original = subprocess.Popen

        def spawn(*args, **kwargs):
            process = original(*args, **kwargs)
            self.spawned.append(process)
            return process

        patch = mock.patch("system.runner.subprocess.Popen", side_effect=spawn)
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self):
        for process in self.spawned:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
        self.assertFalse(
            [
                thread.name
                for thread in threading.enumerate()
                if thread.name.startswith("dev-setup-pipe-")
            ]
        )

    def tree(self, *, exit_parent=False):
        child_path, grandchild_path = (
            self.root / "child.json",
            self.root / "grandchild.json",
        )
        leaf = "import os,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN) if os.name!='nt' else None; time.sleep(30)"
        child = (
            "import os,signal,subprocess,sys,time; from pathlib import Path; "
            "signal.signal(signal.SIGTERM,signal.SIG_IGN) if os.name!='nt' else None; "
            f"p=subprocess.Popen([sys.executable,'-I','-c',{leaf!r}]); "
            f"Path({str(grandchild_path)!r}).write_text(str(p.pid)); time.sleep(30)"
        )
        code = (
            "import subprocess,sys,time; from pathlib import Path; "
            f"p=subprocess.Popen([sys.executable,'-I','-c',{child!r}],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
            f"Path({str(child_path)!r}).write_text(str(p.pid)); "
            f"path=Path({str(grandchild_path)!r}); "
            "deadline=time.monotonic()+5\n"
            "while not path.exists() and time.monotonic()<deadline: time.sleep(0.01)\n"
            "assert path.exists()\n"
            "print('tree-ready',flush=True)\n"
            + ("sys.exit(0)" if exit_parent else "time.sleep(30)")
        )
        return code, child_path, grandchild_path

    def assert_tree_dead(self, child_path, grandchild_path):
        pids = [int(path.read_text()) for path in (child_path, grandchild_path)]
        deadline = time.monotonic() + 3
        while any(alive(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertFalse([pid for pid in pids if alive(pid)])

    def test_cancellation_kills_owned_children_and_grandchildren(self):
        code, child, grandchild = self.tree()
        cancel = threading.Event()

        def observe(snapshot):
            if "tree-ready" in snapshot.text:
                cancel.set()

        result = self.runner.run(
            fixture_command(code, timeout_seconds=10), cancel=cancel, observe=observe
        )
        self.assertEqual(result.error.code, ErrorCode.CANCELLED)
        self.assert_tree_dead(child, grandchild)
        self.assertIsNotNone(self.spawned[0].returncode)

    def test_normal_parent_exit_does_not_leak_background_descendants(self):
        code, child, grandchild = self.tree(exit_parent=True)
        result = self.runner.run(fixture_command(code, timeout_seconds=10))
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(result.exit_code, 0)
        self.assert_tree_dead(child, grandchild)

    def test_timeout_kills_ignoring_tree_after_parent_exits_on_termination(self):
        code, child, grandchild = self.tree()
        expired = threading.Event()
        monotonic = time.monotonic

        def clock():
            return monotonic() + (20 if expired.is_set() else 0)

        def observe(snapshot):
            if "tree-ready" in snapshot.text:
                expired.set()

        with mock.patch("system.runner.time.monotonic", clock):
            result = self.runner.run(
                fixture_command(code, timeout_seconds=10), observe=observe
            )
        self.assertEqual(result.error.code, ErrorCode.TIMEOUT)
        self.assert_tree_dead(child, grandchild)

    def test_large_concurrent_streams_with_slow_observer_remain_bounded(self):
        code = "import os,threading\ndef write(fd,byte):\n for _ in range(512): os.write(fd,byte*8192)\na=threading.Thread(target=write,args=(1,b'x'));b=threading.Thread(target=write,args=(2,b'y'));a.start();b.start();a.join();b.join()"
        events = []

        def observe(snapshot):
            events.append(snapshot)
            time.sleep(0.002)

        self.runner.capture_bytes = 4096
        result = self.runner.run(
            fixture_command(code, timeout_seconds=10), observe=observe
        )
        self.assertTrue(result.succeeded, result.error)
        self.assertEqual(len(result.stdout), 4096)
        self.assertEqual(len(result.stderr), 4096)
        self.assertTrue(result.output_truncated)
        self.assertLessEqual(len(events), 6)
        self.assertEqual({event.stream for event in events}, {"stdout", "stderr"})

    def test_pipe_read_exception_is_sanitized_and_does_not_leak_process_or_reader(self):
        with mock.patch(
            "system.runner.read_available",
            side_effect=RuntimeError("token=pipe-secret private-path"),
        ):
            result = self.runner.run(
                fixture_command("import time;time.sleep(30)", timeout_seconds=2)
            )
        self.assertEqual(result.error.code, ErrorCode.COMMAND_FAILED)
        self.assertIn(
            DiagnosticCode.PIPE_READ_FAILED,
            [event.code for event in result.diagnostics],
        )
        self.assertNotIn("pipe-secret", repr(result))
        self.assertIsNotNone(self.spawned[0].returncode)

    def test_pipe_failure_at_final_eof_cannot_be_misreported_as_exit_zero_success(self):
        release = threading.Event()
        test = self

        class BoundaryQueue(queue.Queue):
            reads = 0

            def get(self, *args, **kwargs):
                self.reads += 1
                if self.reads == 2:
                    test.spawned[0].wait(timeout=3)
                    release.set()
                return super().get(*args, **kwargs)

        def read(descriptor):
            if threading.current_thread().name.endswith("-stderr"):
                release.wait(3)
                raise OSError("sensitive late pipe failure")
            return b""

        with (
            mock.patch("system.runner.queue.Queue", BoundaryQueue),
            mock.patch("system.runner.read_available", read),
        ):
            result = self.runner.run(fixture_command("pass"))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.error.code, ErrorCode.COMMAND_FAILED)
        self.assertIn(
            DiagnosticCode.PIPE_READ_FAILED,
            [event.code for event in result.diagnostics],
        )

    def test_reader_start_failure_still_reaps_child_and_started_readers(self):
        original = threading.Thread.start
        calls = 0

        def start(thread):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("secret reader setup")
            return original(thread)

        with mock.patch("system.runner.threading.Thread.start", start):
            result = self.runner.run(fixture_command("import time;time.sleep(30)"))
        self.assertEqual(result.error.code, ErrorCode.COMMAND_FAILED)
        self.assertIn(
            DiagnosticCode.PROCESS_MONITOR_FAILED,
            [event.code for event in result.diagnostics],
        )
        self.assertIsNotNone(self.spawned[0].returncode)

    @unittest.skipUnless(sys.platform == "win32", "Windows suspended job assignment")
    def test_job_assignment_failure_refuses_execution_before_first_instruction(self):
        marker = self.root / "must-not-exist"
        code = f"from pathlib import Path;Path({str(marker)!r}).write_text('executed')"
        with mock.patch(
            "system.runner.ProcessScope.attach",
            side_effect=RuntimeError("secret job context"),
        ):
            result = self.runner.run(fixture_command(code))
        self.assertEqual(result.error.code, ErrorCode.PROCESS_CONTROL)
        self.assertFalse(marker.exists())
        self.assertIsNotNone(self.spawned[0].returncode)

    def test_unconfirmed_cleanup_cannot_be_reported_as_success(self):
        from system.processes import ProcessScope

        original = ProcessScope.stop

        def unconfirmed(scope, process):
            original(scope, process)
            return False

        with mock.patch("system.runner.ProcessScope.stop", unconfirmed):
            result = self.runner.run(fixture_command("print('ok')"))
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.error.code, ErrorCode.PROCESS_CONTROL)

    def test_pipe_close_failure_preserves_exit_code_and_cleans_other_handles(self):
        original = subprocess.Popen

        def spawn(*args, **kwargs):
            process = original(*args, **kwargs)
            self.spawned.append(process)
            close = process.stdout.close

            def failed_close():
                close()
                raise RuntimeError("private close state")

            process.stdout.close = failed_close
            return process

        with mock.patch("system.runner.subprocess.Popen", spawn):
            result = self.runner.run(fixture_command("print('ok')"))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.error.code, ErrorCode.PROCESS_CONTROL)
        self.assertIn(
            DiagnosticCode.PIPE_CLEANUP_FAILED,
            [event.code for event in result.diagnostics],
        )
        self.assertTrue(self.spawned[0].stderr.closed)
        self.assertNotIn("private close state", repr(result))

    def test_diagnostic_recording_failure_cannot_replace_native_success(self):
        with mock.patch(
            "system.diagnostics.session_diagnostics.record",
            side_effect=RuntimeError("secret diagnostic context"),
        ):
            result = self.runner.run(
                fixture_command("print('ok')"),
                observe=mock.Mock(side_effect=RuntimeError("private observer")),
            )
        self.assertTrue(result.succeeded)
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(result.diagnostics)

    @unittest.skipUnless(sys.platform == "win32", "Windows job kill-on-owner-exit")
    def test_abrupt_runner_process_exit_closes_job_and_kills_owned_tree(self):
        code, child, grandchild = self.tree()
        script = (
            "import os\nfrom system.runner import CommandRunner\n"
            "from tests.integration.test_process_failures import fixture_command\n"
            "def observe(snapshot):\n if 'tree-ready' in snapshot.text: os._exit(23)\n"
            f"CommandRunner().run(fixture_command({code!r},timeout_seconds=10),observe=observe)\n"
        )
        from tests.paths import PROJECT_ROOT

        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            shell=False,
        )
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assert_tree_dead(child, grandchild)
