"""Application data migration and privacy checks use disposable databases only."""

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from persistence.events import SafeEventLog
from persistence.store import DesktopSettings, StateError, StateStore
from services.records import AuditRecord


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "app" / "state.sqlite3"

    def record(self, operation="operation-one", **kwargs):
        now = datetime(2026, 11, 12, tzinfo=timezone.utc)
        return replace(
            AuditRecord(
                operation,
                "git",
                "install",
                "homebrew",
                "homebrew-core",
                "2.50",
                "succeeded",
                now,
                now,
                0,
            ),
            **kwargs,
        )

    def test_settings_and_history_survive_reopen_without_output_storage(self):
        store = StateStore(self.path)
        self.assertEqual(store.load_settings(), DesktopSettings())
        store.save_settings(DesktopSettings("dark", 600, False))
        store.append_audit((self.record(),))
        store.close()
        reopened = StateStore(self.path)
        self.assertEqual(reopened.load_settings(), DesktopSettings("dark", 600, False))
        self.assertEqual(reopened.load_history(), (self.record(),))
        with closing(sqlite3.connect(self.path, isolation_level=None)) as database:
            columns = [row[1] for row in database.execute("PRAGMA table_info(audit)")]
        self.assertNotIn("stdout", columns)
        self.assertNotIn("stderr", columns)
        self.assertNotIn("environment", columns)

    def test_v1_history_migrates_to_v2_without_losing_records(self):
        self.path.parent.mkdir()
        with closing(sqlite3.connect(self.path, isolation_level=None)) as database:
            database.execute(
                "CREATE TABLE audit(operation_id TEXT PRIMARY KEY, package_id TEXT, action TEXT, backend TEXT, source_id TEXT, version TEXT, state TEXT, approved_at TEXT, finished_at TEXT, exit_code INTEGER)"
            )
            database.execute("PRAGMA user_version=1")
            database.execute(
                "INSERT INTO audit VALUES(?,?,?,?,?,?,?,?,?,?)",
                StateStore._values(self.record()),
            )
        store = StateStore(self.path)
        self.assertEqual(store.load_history(), (self.record(),))
        self.assertEqual(store.load_settings(), DesktopSettings())
        with closing(sqlite3.connect(self.path, isolation_level=None)) as database:
            self.assertEqual(database.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_failed_migration_rolls_back_schema_and_version(self):
        self.path.parent.mkdir()
        with closing(sqlite3.connect(self.path, isolation_level=None)) as database:
            database.execute(
                "CREATE TABLE audit(operation_id TEXT PRIMARY KEY, package_id TEXT, action TEXT, backend TEXT, source_id TEXT, version TEXT, state TEXT, approved_at TEXT, finished_at TEXT, exit_code INTEGER)"
            )
            database.execute("PRAGMA user_version=1")

        def fail(database):
            database.execute("CREATE TABLE partial(value TEXT)")
            raise sqlite3.OperationalError("sensitive-machine-path")

        with mock.patch.object(StateStore, "_create_settings", side_effect=fail):
            with self.assertRaises(StateError) as caught:
                StateStore(self.path)
        self.assertNotIn("sensitive-machine-path", str(caught.exception))
        with closing(sqlite3.connect(self.path, isolation_level=None)) as database:
            self.assertEqual(database.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertFalse(
                database.execute(
                    "SELECT name FROM sqlite_master WHERE name='partial'"
                ).fetchall()
            )

    def test_future_and_unversioned_databases_are_preserved(self):
        self.path.parent.mkdir()
        for version in (0, 99):
            with closing(sqlite3.connect(self.path, isolation_level=None)) as database:
                database.execute("CREATE TABLE IF NOT EXISTS unrelated(value TEXT)")
                database.execute(f"PRAGMA user_version={version}")
            with self.assertRaises(StateError):
                StateStore(self.path)
            with closing(sqlite3.connect(self.path, isolation_level=None)) as database:
                self.assertEqual(
                    database.execute("PRAGMA user_version").fetchone()[0], version
                )
                self.assertTrue(
                    database.execute(
                        "SELECT name FROM sqlite_master WHERE name='unrelated'"
                    ).fetchone()
                )

    def test_metadata_validation_and_redaction(self):
        store = StateStore(self.path)
        for record in (
            self.record(state="installing"),
            self.record(operation="bad\nidentifier"),
            self.record(version="x" * 257),
        ):
            with self.assertRaises(ValueError):
                store.append_audit((record,))
        store.append_audit((self.record(version="token=do-not-store"),))
        self.assertEqual(store.load_history()[0].version, "token=[REDACTED]")
        self.assertNotIn(b"do-not-store", self.path.read_bytes())

    def test_idempotent_history_cannot_rewrite_an_operation(self):
        store = StateStore(self.path)
        store.append_audit((self.record(),))
        store.append_audit((self.record(),))
        with self.assertRaises(StateError):
            store.append_audit((self.record("another"), self.record(version="changed")))
        self.assertEqual(store.load_history(), (self.record(),))

    def test_history_is_newest_first_and_can_be_cleared(self):
        store = StateStore(self.path)
        first = self.record()
        second = self.record(
            "second", finished_at=first.finished_at + timedelta(seconds=1)
        )
        store.append_audit((first, second))
        self.assertEqual(store.load_history(), (second, first))
        store.clear_history()
        self.assertFalse(store.load_history())

    def test_settings_validation_rejects_unknown_values_and_implicit_booleans(self):
        for args in (
            ("custom", 250, True),
            ("light", 100, True),
            ("light", True, True),
            ("light", 250, 1),
        ):
            with self.assertRaises(ValueError):
                DesktopSettings(*args)

    def test_corrupt_database_and_later_connection_errors_are_safe(self):
        self.path.parent.mkdir()
        self.path.write_bytes(b"not sqlite; password=do-not-echo")
        with self.assertRaises(StateError) as caught:
            StateStore(self.path)
        self.assertNotIn("do-not-echo", str(caught.exception))
        self.path.unlink()
        store = StateStore(self.path)
        with mock.patch(
            "persistence.store.sqlite3.connect",
            side_effect=sqlite3.OperationalError("machine-path"),
        ):
            with self.assertRaises(StateError) as caught:
                store.load_history()
        self.assertNotIn("machine-path", str(caught.exception))

    def test_closed_store_and_symlink_are_not_used(self):
        store = StateStore(self.path)
        store.close()
        with self.assertRaises(StateError):
            store.load_settings()
        link = self.path.with_name("link.sqlite3")
        try:
            link.symlink_to(self.path)
        except OSError:
            self.skipTest("Symlink creation is not permitted by this host")
        with self.assertRaises(StateError):
            StateStore(link)

    def test_event_log_accepts_fixed_metadata_only_and_stays_bounded(self):
        path = Path(self.directory.name) / "events.log"
        log = SafeEventLog(path)
        log.record("operation_finished", code="failed", operation_id="operation-one")
        value = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(set(value), {"timestamp", "event", "code", "operation_id"})
        with self.assertRaises(ValueError):
            log.record("password=do-not-log")
        for _ in range(1500):
            log.record("operation_finished", code="failed")
        log.close()
        self.assertLessEqual(path.stat().st_size, 65536)
        self.assertTrue(path.with_name("events.log.1").exists())
        self.assertNotIn("do-not-log", path.read_text(encoding="utf-8"))
