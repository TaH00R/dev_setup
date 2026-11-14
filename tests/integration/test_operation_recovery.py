"""A subprocess dies during a fake native command; restart never reruns it."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from models.lifecycle import LifecycleState, PlanOutcome, aggregate
from persistence.store import StateStore
from tests.paths import PROJECT_ROOT


class CrashRecoveryTests(unittest.TestCase):
    def test_crash_after_one_verified_item_retains_partial_plan_and_active_evidence(
        self,
    ):
        script = """
import os
import sys
from pathlib import Path
from persistence.store import StateStore
from services.records import PackageRequest, PlanConsent
from tests.fixtures.desktop import DesktopFixture
root = Path(sys.argv[1])
fixture = DesktopFixture(root)
store = StateStore(root / 'state.sqlite3')
fixture.services.operations.lifecycle_store = store
plan = fixture.services.planner.preview((PackageRequest('git'), PackageRequest('cmake')))
original = fixture.runner.run.side_effect
mutations = 0
def run(command, **kwargs):
    global mutations
    if command.mutates:
        mutations += 1
        if mutations == 2:
            records = store.load_lifecycle()
            assert any(record.state.value == 'succeeded' for record in records)
            assert any(record.state.value == 'installing' for record in records)
            os._exit(23)
    return original(command, **kwargs)
fixture.runner.run.side_effect = run
fixture.services.operations.execute(plan, consent=PlanConsent(plan, True))
raise AssertionError('Expected fake native crash')
"""
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, "-c", script, directory],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=20,
                shell=False,
            )
            self.assertEqual(result.returncode, 23, result.stderr)
            store = StateStore(Path(directory) / "state.sqlite3")
            try:
                interrupted = store.interrupt_active()
                self.assertEqual(len(interrupted), 1)
                self.assertEqual(interrupted[0].state, LifecycleState.INTERRUPTED)
                self.assertEqual(interrupted[0].phase, LifecycleState.INSTALLING)
                self.assertEqual(interrupted[0].verification, "unknown")
                records = store.load_lifecycle()
                self.assertEqual(
                    aggregate(tuple(record.state.value for record in records)),
                    PlanOutcome.PARTIAL_SUCCESS,
                )
                self.assertEqual(len({record.execution_id for record in records}), 1)
                self.assertEqual(len({record.operation_id for record in records}), 2)
            finally:
                store.close()
