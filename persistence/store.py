"""Versioned SQLite settings and output-free audit history.

Every method uses a short-lived connection under a lock. No database connection
or app-data directory is created until StateStore is explicitly constructed.
"""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from models._validation import identifier, text
from models.lifecycle import LifecycleRecord, LifecycleState
from models.planning import aware_timestamp
from services.records import AuditRecord
from system.commands import Redactor
from system.coordination import CoordinationError, NativeCoordinator

SCHEMA_VERSION = 3
_AUDIT_COLUMNS = (
    "operation_id",
    "package_id",
    "action",
    "backend",
    "source_id",
    "version",
    "state",
    "approved_at",
    "finished_at",
    "exit_code",
)


class StateError(ValueError):
    """Safe display error; never wraps raw database paths or exception text."""


@dataclass(frozen=True)
class DesktopSettings:
    theme: str = "dark"
    search_delay_ms: int = 250
    live_search: bool = True

    def __post_init__(self) -> None:
        if self.theme not in {"light", "dark"}:
            raise ValueError("theme must be light or dark")
        if (
            type(self.search_delay_ms) is not int
            or not 150 <= self.search_delay_ms <= 1000
        ):
            raise ValueError("search delay must be between 150 and 1000 ms")
        if type(self.live_search) is not bool:
            raise ValueError("live search preference must be a boolean")


class StateStore:
    """App-owned database. History loads newest-first and retains 2000 records."""

    def __init__(self, path: Path) -> None:
        self.coordinator = NativeCoordinator()
        path = Path(path)
        if path.is_symlink():
            raise StateError("A symlink cannot be used as the application database.")
        self.path = path.absolute()
        self._lock = RLock()
        self._closed = False
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not self.path.exists():
                try:
                    descriptor = os.open(
                        self.path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600
                    )
                except FileExistsError:
                    pass
                else:
                    os.close(descriptor)
            with self._connection() as database:
                self._migrate(database)
        except (OSError, sqlite3.Error):
            raise StateError(
                "Application state could not be opened or migrated; the existing database was retained."
            ) from None

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            if self._closed:
                raise StateError("Application state is closed.")
            if self.path.is_symlink():
                raise StateError(
                    "The application database changed to a symlink; access was refused."
                )
            try:
                database = sqlite3.connect(self.path, timeout=3.0, isolation_level=None)
            except sqlite3.Error:
                raise StateError(
                    "Application state could not be opened; connection details were omitted."
                ) from None
            try:
                database.execute("PRAGMA foreign_keys=ON")
                yield database
            except sqlite3.Error:
                if database.in_transaction:
                    database.rollback()
                raise StateError(
                    "Application state could not be read or saved; no raw database error was recorded."
                ) from None
            finally:
                database.close()

    @staticmethod
    def _create_settings(database: sqlite3.Connection) -> None:
        database.execute("""CREATE TABLE settings (
            singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
            theme TEXT NOT NULL CHECK(theme IN ('light','dark')),
            search_delay_ms INTEGER NOT NULL CHECK(search_delay_ms BETWEEN 150 AND 1000),
            live_search INTEGER NOT NULL CHECK(live_search IN (0,1)))""")

    @classmethod
    def _migrate(cls, database: sqlite3.Connection) -> None:
        version = database.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise StateError(
                "This database belongs to a newer application version; downgrade was refused."
            )
        database.execute("BEGIN IMMEDIATE")
        try:
            if version == 0:
                if database.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall():
                    raise StateError(
                        "An unversioned existing database was not modified."
                    )
                database.execute("""CREATE TABLE audit (
                    operation_id TEXT PRIMARY KEY, package_id TEXT NOT NULL,
                    action TEXT NOT NULL, backend TEXT NOT NULL, source_id TEXT NOT NULL,
                    version TEXT, state TEXT NOT NULL, approved_at TEXT,
                    finished_at TEXT NOT NULL, exit_code INTEGER)""")
            else:
                columns = tuple(
                    row[1] for row in database.execute("PRAGMA table_info(audit)")
                )
                if columns != _AUDIT_COLUMNS:
                    raise StateError(
                        "The existing history schema is unsupported; migration was refused."
                    )
            if version < 2:
                cls._create_settings(database)
            else:
                columns = tuple(
                    row[1] for row in database.execute("PRAGMA table_info(settings)")
                )
                if columns != ("singleton", "theme", "search_delay_ms", "live_search"):
                    raise StateError("The existing settings schema is unsupported.")
            if version < 3:
                database.execute(
                    "CREATE TABLE lifecycle (operation_id TEXT PRIMARY KEY, record TEXT NOT NULL)"
                )
            elif tuple(
                row[1] for row in database.execute("PRAGMA table_info(lifecycle)")
            ) != ("operation_id", "record"):
                raise StateError("The existing lifecycle schema is unsupported.")
            database.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            database.commit()
        except Exception:
            database.rollback()
            raise

    def load_settings(self) -> DesktopSettings:
        with self._connection() as database:
            row = database.execute(
                "SELECT theme, search_delay_ms, live_search FROM settings WHERE singleton=1"
            ).fetchone()
        if row is None:
            return DesktopSettings()
        try:
            if row[2] not in (0, 1):
                raise ValueError("invalid flag")
            return DesktopSettings(row[0], row[1], bool(row[2]))
        except ValueError:
            raise StateError(
                "Saved preferences failed validation; defaults may be used without overwriting them."
            ) from None

    def save_settings(self, settings: DesktopSettings) -> None:
        if not isinstance(settings, DesktopSettings):
            raise ValueError("settings must be a validated DesktopSettings record")
        with self._connection() as database:
            database.execute("BEGIN IMMEDIATE")
            database.execute(
                """INSERT INTO settings VALUES(1, ?, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET theme=excluded.theme,
                search_delay_ms=excluded.search_delay_ms, live_search=excluded.live_search""",
                (settings.theme, settings.search_delay_ms, int(settings.live_search)),
            )
            database.commit()

    @staticmethod
    def _validated(record: AuditRecord) -> AuditRecord:
        if not isinstance(record, AuditRecord):
            raise ValueError("history accepts AuditRecord metadata only")
        for field in ("operation_id", "package_id", "action", "backend", "source_id"):
            value = getattr(record, field)
            identifier(value, field)
            if len(value) > 128:
                raise ValueError("audit identifier exceeds its bound")
        if record.state not in {"succeeded", "failed", "cancelled", "skipped"}:
            raise ValueError("audit history requires a terminal state")
        aware_timestamp(record.finished_at, "audit finish time")
        if record.approved_at:
            aware_timestamp(record.approved_at, "audit approval time")
            if record.approved_at > record.finished_at:
                raise ValueError("audit approval cannot follow completion")
        if record.exit_code is not None and (
            type(record.exit_code) is not int
            or not -(2**31) <= record.exit_code < 2**32
        ):
            raise ValueError("audit exit code must be a bounded process exit status")
        if record.version is not None:
            text(record.version, "audit version")
            if len(record.version) > 256:
                raise ValueError("audit version exceeds its bound")
            record = replace(record, version=Redactor().clean(record.version))
        return record

    @staticmethod
    def _values(record: AuditRecord) -> tuple[object, ...]:
        return (
            record.operation_id,
            record.package_id,
            record.action,
            record.backend,
            record.source_id,
            record.version,
            record.state,
            record.approved_at.astimezone(timezone.utc).isoformat()
            if record.approved_at
            else None,
            record.finished_at.astimezone(timezone.utc).isoformat(),
            record.exit_code,
        )

    def append_audit(self, records: tuple[AuditRecord, ...]) -> None:
        if not isinstance(records, tuple) or len(records) > 1000:
            raise ValueError("audit writes require at most 1000 immutable records")
        validated = tuple(self._validated(record) for record in records)
        with self._connection() as database:
            database.execute("BEGIN IMMEDIATE")
            try:
                for record in validated:
                    values = self._values(record)
                    existing = database.execute(
                        "SELECT * FROM audit WHERE operation_id=?",
                        (record.operation_id,),
                    ).fetchone()
                    if existing is not None:
                        if tuple(existing) != values:
                            raise StateError(
                                "An existing audit operation cannot be rewritten with different metadata."
                            )
                        continue
                    database.execute(
                        "INSERT INTO audit VALUES(?,?,?,?,?,?,?,?,?,?)", values
                    )
                database.execute(
                    "DELETE FROM audit WHERE rowid NOT IN (SELECT rowid FROM audit ORDER BY finished_at DESC, rowid DESC LIMIT 2000)"
                )
                database.commit()
            except Exception:
                database.rollback()
                raise

    def load_history(self, limit: int = 200) -> tuple[AuditRecord, ...]:
        if type(limit) is not int or not 1 <= limit <= 2000:
            raise ValueError("history limit must be between 1 and 2000")
        with self._connection() as database:
            rows = database.execute(
                "SELECT * FROM audit ORDER BY finished_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        try:
            return tuple(
                self._validated(
                    AuditRecord(
                        row[0],
                        row[1],
                        row[2],
                        row[3],
                        row[4],
                        row[5],
                        row[6],
                        datetime.fromisoformat(row[7]) if row[7] else None,
                        datetime.fromisoformat(row[8]),
                        row[9],
                    )
                )
                for row in rows
            )
        except (ValueError, TypeError):
            raise StateError(
                "Saved audit metadata failed validation; no raw database content was displayed."
            ) from None

    def clear_history(self) -> None:
        with self._connection() as database:
            database.execute("BEGIN IMMEDIATE")
            database.execute("DELETE FROM audit")
            records = self._lifecycle_rows(database)
            protected_executions = {
                record.execution_id
                for record in records
                if not record.state.terminal or record.needs_recovery
            }
            for record in records:
                if record.execution_id not in protected_executions:
                    database.execute(
                        "DELETE FROM lifecycle WHERE operation_id=?",
                        (record.operation_id,),
                    )
            database.commit()

    @staticmethod
    def _lifecycle_rows(database: sqlite3.Connection) -> tuple[LifecycleRecord, ...]:
        try:
            return tuple(
                LifecycleRecord.from_json(row[0])
                for row in database.execute(
                    "SELECT record FROM lifecycle ORDER BY rowid"
                )
            )
        except (ValueError, TypeError, KeyError):
            raise StateError(
                "Saved lifecycle evidence failed validation; raw content was omitted."
            ) from None

    def load_lifecycle(self) -> tuple[LifecycleRecord, ...]:
        with self._connection() as database:
            return self._lifecycle_rows(database)

    def save_lifecycle(self, record: LifecycleRecord) -> None:
        if not isinstance(record, LifecycleRecord):
            raise ValueError("Lifecycle writes require validated metadata.")
        with self._connection() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                "SELECT record FROM lifecycle WHERE operation_id=?",
                (record.operation_id,),
            ).fetchone()
            if row is not None:
                previous = LifecycleRecord.from_json(row[0])
                identity_fields = (
                    "execution_id",
                    "plan_id",
                    "plan_identity",
                    "family",
                    "package_id",
                    "native_id",
                    "backend",
                    "source_id",
                    "host_identity",
                    "action",
                    "expected_version",
                    "before_identity",
                    "approved_at",
                    "created_at",
                )
                if (
                    any(
                        getattr(previous, field) != getattr(record, field)
                        for field in identity_fields
                    )
                    or record.updated_at < previous.updated_at
                ):
                    raise StateError(
                        "Lifecycle identity or chronology changed; write refused."
                    )
                if (
                    previous.state.terminal
                    and previous.state != LifecycleState.INTERRUPTED
                    and previous.detail != "inspection_required"
                    and previous != record
                ):
                    raise StateError("Completed execution evidence is immutable.")
                if previous.reconciled_at is not None and previous != record:
                    raise StateError("Reconciled evidence is immutable.")
                if not previous.state.terminal and previous != record:
                    successor = previous.advance(
                        record.state, verification=record.verification
                    )
                    if successor.state != record.state:
                        raise StateError(
                            "Cancellation requests cannot be erased before a terminal outcome."
                        )
                    if previous.mutation_started and not record.mutation_started:
                        raise StateError("Native mutation evidence cannot be erased.")
                    if (
                        previous.cancel_requested_at is not None
                        and previous.cancel_requested_at != record.cancel_requested_at
                    ):
                        raise StateError(
                            "Cancellation request evidence cannot be erased."
                        )
                    if (
                        previous.started_at is not None
                        and previous.started_at != record.started_at
                    ):
                        raise StateError(
                            "Execution start evidence cannot be rewritten."
                        )
                    if record.state.terminal and record.phase != previous.phase:
                        raise StateError(
                            "The last execution phase cannot change at completion."
                        )
                if previous.state.terminal and (
                    record.state != previous.state
                    or record.exit_code != previous.exit_code
                    or record.phase != previous.phase
                    or record.mutation_started != previous.mutation_started
                    or record.detail != previous.detail
                    or record.started_at != previous.started_at
                    or record.finished_at != previous.finished_at
                    or record.cancel_requested_at != previous.cancel_requested_at
                ):
                    raise StateError(
                        "A completed lifecycle cannot be rewritten or resumed."
                    )
            database.execute(
                "INSERT INTO lifecycle VALUES(?,?) ON CONFLICT(operation_id) DO UPDATE SET record=excluded.record",
                (record.operation_id, record.to_json()),
            )
            database.commit()

    def interrupt_active(self) -> tuple[LifecycleRecord, ...]:
        """Startup recovery only: preserve evidence, never execute/retry anything."""
        try:
            with self.coordinator.hold():
                return self._interrupt_active()
        except CoordinationError as error:
            raise StateError(error.error.message) from None

    def _interrupt_active(self) -> tuple[LifecycleRecord, ...]:
        with self._connection() as database:
            database.execute("BEGIN IMMEDIATE")
            records = self._lifecycle_rows(database)
            for record in records:
                if not record.state.terminal:
                    interrupted = record.advance(
                        LifecycleState.INTERRUPTED,
                        verification="unknown",
                        detail="outcome_unknown",
                    )
                    database.execute(
                        "UPDATE lifecycle SET record=? WHERE operation_id=?",
                        (interrupted.to_json(), record.operation_id),
                    )
            database.commit()
            return tuple(
                record
                for record in self._lifecycle_rows(database)
                if record.needs_recovery
            )

    def close(self) -> None:
        with self._lock:
            self._closed = True
