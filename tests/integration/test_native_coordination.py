"""Real kernel lock contention and crash cleanup using disposable Python processes."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from models.errors import ErrorCode
from models.lifecycle import LifecycleState
from persistence.store import StateError, StateStore
from system.coordination import CoordinationError, NativeCoordinator
from tests.fixtures.desktop import DesktopFixture
from tests.fixtures.lifecycle import active_run
from tests.paths import PROJECT_ROOT


class ProcessCoordinationTests(unittest.TestCase):
    def test_other_process_excludes_startup_recovery_and_kernel_lock_dies_with_owner(
        self,
    ):
        script = """
import sys
from pathlib import Path
from system.coordination import NativeCoordinator
with NativeCoordinator(Path(sys.argv[1]), timeout=0).hold():
    print('locked', flush=True)
    sys.stdin.read(1)
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = StateStore(root / "state.sqlite3")
            try:
                fixture = DesktopFixture(root)
                active_run(fixture, store)
                store.coordinator = NativeCoordinator(root / "locks", timeout=0)
                process = subprocess.Popen(
                    [sys.executable, "-c", script, str(root / "locks")],
                    cwd=PROJECT_ROOT,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    shell=False,
                )
                try:
                    self.assertEqual(process.stdout.readline().strip(), "locked")
                    with self.assertRaises(CoordinationError) as error:
                        with NativeCoordinator(root / "locks", timeout=0).hold():
                            self.fail("Concurrent process acquired native access")
                    self.assertEqual(error.exception.error.code, ErrorCode.BUSY)
                    with self.assertRaises(StateError):
                        store.interrupt_active()
                    self.assertEqual(
                        store.load_lifecycle()[0].state, LifecycleState.INSTALLING
                    )
                finally:
                    process.kill()
                    process.communicate(timeout=10)
                interrupted = store.interrupt_active()
                self.assertEqual(interrupted[0].state, LifecycleState.INTERRUPTED)
                with NativeCoordinator(root / "locks", timeout=0).hold():
                    pass
                self.assertFalse(fixture.mutations)
            finally:
                store.close()
