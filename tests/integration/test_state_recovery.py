"""Real SQLite transactions in disposable files; no native package changes."""

import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest import mock

from models.lifecycle import LifecycleState
from models.planning import utc_now
from persistence.store import DesktopSettings, StateError, StateStore
from services.records import AuditRecord
from tests.fixtures.desktop import DesktopFixture
from tests.fixtures.lifecycle import active_run
from tests.paths import PROJECT_ROOT


class StateRecoveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / "state.sqlite3"
        self.fixture = DesktopFixture(self.root)
        self.store = StateStore(self.path)
        self.addCleanup(self.store.close)
        self.settings = DesktopSettings("light", 650, False)
        self.store.save_settings(self.settings)
        now = utc_now()
        self.audit = AuditRecord(
            "prior-operation",
            "git",
            "install",
            "homebrew",
            "homebrew-core",
            "1.0",
            "succeeded",
            now,
            now,
            0,
        )
        self.store.append_audit((self.audit,))

    def legacy_v3(self):
        run, operation_id = active_run(self.fixture, self.store, ("git", "cmake"))
        self.store.close()
        with closing(sqlite3.connect(self.path, isolation_level=None)) as database:
            for record in run.records.values():
                data = json.loads(record.to_json())
                del data["catalog_revision"]
                database.execute(
                    "UPDATE lifecycle SET record=? WHERE operation_id=?",
                    (json.dumps(data), record.operation_id),
                )
            database.execute("PRAGMA user_version=3")
        return run, operation_id

    def raw_rows(self):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as database:
            return database.execute(
                "SELECT operation_id, record FROM lifecycle ORDER BY rowid"
            ).fetchall()

    def reopen(self, **kwargs):
        store = StateStore(self.path, **kwargs)
        self.addCleanup(store.close)
        return store

    def assert_prior(self, store):
        self.assertEqual(store.load_settings(), self.settings)
        self.assertEqual(store.load_history(), (self.audit,))

    def test_v3_migration_preserves_evidence_and_marks_old_revision_unknown(self):
        run, _ = self.legacy_v3()
        expected = tuple(
            replace(record, catalog_revision=None) for record in run.records.values()
        )
        reopened = self.reopen()
        self.assert_prior(reopened)
        self.assertEqual(reopened.load_lifecycle(), expected)
        interrupted = reopened.interrupt_active()
        self.assertEqual(len(interrupted), 2)
        self.assertEqual(interrupted[0].phase, LifecycleState.INSTALLING)
        self.assertTrue(interrupted[0].mutation_started)
        self.assertEqual(interrupted[0].catalog_revision, None)
        self.fixture.runner.run.assert_not_called()

    def test_bad_v3_payload_rolls_back_entire_migration_without_sensitive_error(self):
        self.legacy_v3()
        with closing(sqlite3.connect(self.path, isolation_level=None)) as database:
            key, raw = self.raw_rows()[1]
            data = json.loads(raw)
            data["state"] = "password=private-state"
            database.execute(
                "UPDATE lifecycle SET record=? WHERE operation_id=?",
                (json.dumps(data), key),
            )
        original = self.raw_rows()
        with self.assertRaises(StateError) as caught:
            self.reopen()
        self.assertNotIn("private-state", str(caught.exception))
        self.assertEqual(self.raw_rows(), original)
        with closing(sqlite3.connect(self.path, isolation_level=None)) as database:
            self.assertEqual(database.execute("PRAGMA user_version").fetchone()[0], 3)
            self.assertEqual(
                database.execute("SELECT theme FROM settings").fetchone()[0], "light"
            )

    def test_migration_commit_failure_restores_version_and_original_json(self):
        self.legacy_v3()
        original = self.raw_rows()
        connect = sqlite3.connect

        class FailedCommit(sqlite3.Connection):
            def commit(self):
                raise sqlite3.OperationalError("disk full token=private-commit")

        with mock.patch(
            "persistence.store.sqlite3.connect",
            side_effect=lambda *args, **kwargs: connect(
                *args, factory=FailedCommit, **kwargs
            ),
        ):
            with self.assertRaises(StateError) as caught:
                self.reopen()
        self.assertNotIn("private-commit", str(caught.exception))
        self.assertEqual(self.raw_rows(), original)
        with closing(connect(self.path)) as database:
            self.assertEqual(database.execute("PRAGMA user_version").fetchone()[0], 3)
        self.assert_prior(self.reopen())

    def test_simultaneous_migrators_read_version_after_acquiring_write_lock(self):
        self.legacy_v3()
        barrier = threading.Barrier(6)

        def open_store(_):
            barrier.wait(5)
            store = StateStore(self.path)
            try:
                return (
                    store.load_settings(),
                    store.load_history(),
                    store.load_lifecycle(),
                )
            finally:
                store.close()

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = tuple(pool.map(open_store, range(6)))
        self.assertEqual(len(results), 6)
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(results[0][0], self.settings)
        self.assertEqual(len(results[0][2]), 2)

    def test_separate_stores_preserve_concurrent_audits_and_lifecycle(self):
        run, _ = active_run(self.fixture, self.store, ("git", "cmake"))
        records = tuple(run.records.values())
        barrier = threading.Barrier(4)

        def write(index):
            store = StateStore(self.path)
            try:
                barrier.wait(5)
                for number in range(12):
                    store.append_audit(
                        (replace(self.audit, operation_id=f"writer-{index}-{number}"),)
                    )
                    store.save_lifecycle(records[index % 2])
                    store.save_settings(self.settings)
            finally:
                store.close()

        with ThreadPoolExecutor(max_workers=4) as pool:
            tuple(pool.map(write, range(4)))
        self.assertEqual(len(self.store.load_history()), 49)
        self.assertEqual(self.store.load_lifecycle(), records)
        self.assertEqual(self.store.load_settings(), self.settings)

    def test_reserved_writer_allows_reads_and_timeout_preserves_data(self):
        store = self.reopen(busy_timeout=0.05)
        with closing(sqlite3.connect(self.path, isolation_level=None)) as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            blocker.execute("UPDATE settings SET theme='dark'")
            # Readers see the committed snapshot, not another writer's changes.
            self.assert_prior(store)
            started = time.monotonic()
            with self.assertRaises(StateError) as caught:
                store.save_settings(DesktopSettings("dark"))
            self.assertLess(time.monotonic() - started, 2)
            self.assertNotIn(str(self.path), str(caught.exception))
            with self.assertRaises(StateError):
                StateStore(self.path, busy_timeout=0.05)
            blocker.rollback()
        self.assert_prior(store)
        store.save_settings(DesktopSettings("dark"))
        self.assertEqual(store.load_settings().theme, "dark")
        with closing(sqlite3.connect(self.path, isolation_level=None)) as database:
            self.assertEqual(
                database.execute("PRAGMA journal_mode").fetchone()[0], "delete"
            )

    def test_future_schema_is_refused_by_an_already_open_store(self):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as database:
            database.execute("PRAGMA user_version=99")
        with self.assertRaises(StateError):
            self.store.save_settings(DesktopSettings("dark"))
        with self.assertRaises(StateError):
            self.store.clear_history()
        with closing(sqlite3.connect(self.path, isolation_level=None)) as database:
            self.assertEqual(database.execute("PRAGMA user_version").fetchone()[0], 99)
            self.assertEqual(
                database.execute("SELECT theme FROM settings").fetchone()[0], "light"
            )
            self.assertEqual(
                database.execute("SELECT count(*) FROM audit").fetchone()[0], 1
            )

    def test_existing_wal_mode_is_retained_without_changing_policy_for_new_stores(self):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as database:
            self.assertEqual(
                database.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal"
            )
        self.store.save_settings(self.settings)
        active_run(self.fixture, self.store)
        reopened = self.reopen()
        self.assert_prior(reopened)
        self.assertTrue(reopened.load_lifecycle())
        with reopened._connection() as database:
            self.assertEqual(
                database.execute("PRAGMA journal_mode").fetchone()[0], "wal"
            )
            self.assertEqual(database.execute("PRAGMA synchronous").fetchone()[0], 2)

    def test_missing_live_database_is_not_recreated(self):
        self.path.unlink()
        with self.assertRaises(StateError):
            self.store.load_settings()
        self.assertFalse(self.path.exists())

    def test_corrupt_database_bytes_are_preserved(self):
        self.store.close()
        content = b"corrupt database token=private-location"
        self.path.write_bytes(content)
        with self.assertRaises(StateError) as caught:
            self.reopen()
        self.assertNotIn("private-location", str(caught.exception))
        self.assertEqual(self.path.read_bytes(), content)

    def test_corrupt_lifecycle_key_blocks_clear_and_overwrite_without_losing_audit(
        self,
    ):
        run, operation_id = active_run(self.fixture, self.store)
        with closing(sqlite3.connect(self.path, isolation_level=None)) as database:
            data = json.loads(run.records[operation_id].to_json())
            data["operation_id"] = "different-key"
            database.execute("UPDATE lifecycle SET record=?", (json.dumps(data),))
        for action in (
            self.store.load_lifecycle,
            self.store.clear_history,
            lambda: self.store.save_lifecycle(run.records[operation_id]),
        ):
            with self.subTest(action=action), self.assertRaises(StateError):
                action()
        self.assert_prior(self.store)

    def test_real_sqlite_full_rolls_back_audit_batch_and_preserves_lifecycle(self):
        run, _ = active_run(self.fixture, self.store)
        records = tuple(run.records.values())
        connect = sqlite3.connect

        def limited(*args, **kwargs):
            database = connect(*args, **kwargs)
            pages = database.execute("PRAGMA page_count").fetchone()[0]
            database.execute(f"PRAGMA max_page_count={pages}")
            return database

        batch = tuple(
            replace(self.audit, operation_id=f"large-{number}", version="v" * 256)
            for number in range(200)
        )
        with mock.patch("persistence.store.sqlite3.connect", side_effect=limited):
            with self.assertRaises(StateError):
                self.store.append_audit(batch)
        self.assert_prior(self.store)
        self.assertEqual(self.store.load_lifecycle(), records)
        self.assert_prior(self.reopen())

    def test_rollback_failure_cannot_expose_raw_error_or_prevent_connection_close(self):
        connect = sqlite3.connect

        class FailedRollback(sqlite3.Connection):
            def rollback(self):
                raise sqlite3.OperationalError("rollback password=private-cleanup")

        with mock.patch(
            "persistence.store.sqlite3.connect",
            side_effect=lambda *args, **kwargs: connect(
                *args, factory=FailedRollback, **kwargs
            ),
        ):
            with self.assertRaises(StateError) as caught:
                self.store.append_audit(
                    (
                        replace(self.audit, operation_id="must-rollback"),
                        replace(self.audit, version="different"),
                    )
                )
        self.assertNotIn("private-cleanup", str(caught.exception))
        self.assert_prior(self.store)

    def test_process_crash_rolls_back_uncommitted_data_then_recovers_checkpoint(self):
        run, operation_id = active_run(self.fixture, self.store)
        script = """
import os,sys
from pathlib import Path
from persistence.store import StateStore
from models.lifecycle import LifecycleState
store = StateStore(Path(sys.argv[1]))
record = store.load_lifecycle()[0].advance(LifecycleState.VERIFYING)
with store._connection(write=True) as database:
    database.execute('PRAGMA cache_size=1')
    database.execute("UPDATE settings SET theme='dark'")
    database.execute('UPDATE lifecycle SET record=?', (record.to_json(),))
    database.execute('CREATE TABLE uncommitted(payload BLOB)')
    database.execute('INSERT INTO uncommitted VALUES(zeroblob(300000))')
    os._exit(29)
"""
        result = subprocess.run(
            [sys.executable, "-c", script, str(self.path)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            shell=False,
        )
        self.assertEqual(result.returncode, 29, result.stderr)
        reopened = self.reopen()
        self.assert_prior(reopened)
        self.assertEqual(reopened.load_lifecycle(), tuple(run.records.values()))
        interrupted = reopened.interrupt_active()[0]
        self.assertEqual(interrupted.operation_id, operation_id)
        self.assertEqual(interrupted.phase, LifecycleState.INSTALLING)
        self.assertEqual(interrupted.verification, "unknown")
        self.assertEqual(
            interrupted.catalog_revision, self.fixture.services.catalog.revision
        )
        with closing(sqlite3.connect(self.path, isolation_level=None)) as database:
            self.assertIsNone(
                database.execute(
                    "SELECT name FROM sqlite_master WHERE name='uncommitted'"
                ).fetchone()
            )
        self.fixture.runner.run.assert_not_called()

    def test_process_crash_during_migration_preserves_old_schema_until_reopen(self):
        self.legacy_v3()
        original = self.raw_rows()
        script = """
import os,sys,sqlite3
from pathlib import Path
from persistence.store import StateStore
connect = sqlite3.connect
class CrashBeforeCommit(sqlite3.Connection):
    def execute(self, sql, *args):
        cursor = super().execute(sql, *args)
        if sql == 'PRAGMA user_version=4':
            os._exit(31)
        return cursor
sqlite3.connect = lambda *args, **kwargs: connect(*args, factory=CrashBeforeCommit, **kwargs)
StateStore(Path(sys.argv[1]))
raise AssertionError('Expected crash')
"""
        result = subprocess.run(
            [sys.executable, "-c", script, str(self.path)],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            shell=False,
        )
        self.assertEqual(result.returncode, 31, result.stderr)
        with closing(sqlite3.connect(self.path, isolation_level=None)) as database:
            self.assertEqual(database.execute("PRAGMA user_version").fetchone()[0], 3)
        self.assertEqual(self.raw_rows(), original)
        self.assert_prior(self.reopen())
