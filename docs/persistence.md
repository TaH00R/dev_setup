# Local settings, history and diagnostics

Dev Setup keeps desktop preferences and operation lifecycle metadata in an
application-owned SQLite database. Importing the core does not create a database,
read desktop preferences, or start a background process. `StateStore(path)` opens
an explicitly selected database; desktop startup selects its normal location.

| OS | Default database |
| --- | --- |
| Windows | `%LOCALAPPDATA%/DevSetup/state.sqlite3` |
| macOS | `~/Library/Application Support/DevSetup/state.sqlite3` |
| Linux | `$XDG_STATE_HOME/DevSetup/state.sqlite3`, or `~/.local/state/DevSetup/state.sqlite3` when that variable is absent or relative |

`system.state.default_state_path()` calculates this path without creating it. New
database files use owner-only creation permissions on POSIX. Existing directory
permissions and Windows ACLs are retained. Symlink database files are refused.
These files are not encrypted; their schema deliberately excludes secrets.

## Stored preferences

`persistence.store.DesktopSettings` is immutable and validates every field:

| Field | Default | Accepted values |
| --- | --- | --- |
| `theme` | `dark` | `light`, `dark` |
| `search_delay_ms` | `250` | integer from 150 through 1000 |
| `live_search` | `True` | an actual boolean |

`load_settings()` returns defaults when no preference row exists.
`save_settings(settings)` changes only the validated preference row. Invalid saved
data produces a safe `StateError`; callers can show defaults without overwriting
the database. Preferences/history use the state worker. Durable lifecycle
checkpoints run synchronously on the native-operation worker before dispatch;
inspection uses a recovery worker. The store serializes these connections without
blocking the Qt event loop.

## Audit history

`append_audit(tuple_of_records)` accepts completed `services.records.AuditRecord`
objects. It stores operation/package IDs, action, backend/source, version, terminal
state, approval/completion timestamps and exit status. Credential-like text in a
version is redacted. It stores no stdout, stderr, command arguments, environment
values, project files, passwords or tokens. Native output remains transient.

Writes are transactional and limited to 1000 records per call. Replaying the same
operation ID with identical metadata is harmless; changing that operation's
metadata refuses the entire batch. The newest 2000 records are retained.
`load_history(limit=200)` returns records **newest first**, ordered by completion
timestamp and insertion order for ties. The maximum read limit is 2000.
`clear_history()` deletes audit and completed/reconciled lifecycle records while
retaining preferences and all lifecycle evidence for executions with unresolved
interrupted/active items, so partial-plan context is preserved.

The database contains durable execution evidence, not an automatically replayed
queue. Package/runtime operations save all queued IDs, reviewed plan identity,
hashed before-inventory evidence and phase checkpoints before starting commands.
Startup marks nonterminal records interrupted with an unknown outcome. Queue
offers explicit read-only native inspection and acknowledged reconciliation;
it never retries or undoes native changes. A persistence failure after execution
is shown separately from the actual native result. See [lifecycle recovery](lifecycle.md).
Startup takes the shared [native-access guard](native-safety.md) before marking
records interrupted, so it cannot relabel a cooperating instance's active
transaction. Busy startup fails safely without changing those records.

## Schema upgrades and recovery

The SQLite `user_version` is currently 4. Version 1 contains audit history; version
2 adds the singleton settings table; version 3 adds validated lifecycle metadata.
Version 4 adds a catalog revision reference to each lifecycle payload. Package
plans carry a SHA-256 digest of the normalized reviewed catalog; older records
and runtime records retain an explicit unknown (`null`) reference. Migration
never assigns today's catalog to historical operations. It validates every old
payload and its database key before retaining it in the new format. The digest
is a content reference, not a signature, archived catalog or trust upgrade.

A new database creates all three tables. Migrations acquire `BEGIN IMMEDIATE`
**before** reading the schema version, so simultaneous openers cannot migrate
using a stale version. The format conversion and version bump commit in the same
transaction. A failed migration rolls back both
schema/data changes and the version number. A database from a newer version, an
unrecognized schema, or an existing unversioned database is refused without
deleting its tables. There is no automatic destructive downgrade or reset.

Connections are short lived and serialized per store. Every read uses a snapshot;
every write acquires an immediate transaction, rechecks the current schema, and
commits only after the complete operation succeeds. A database upgraded by
another instance is refused by an already-open store. Only explicit construction
may create a database; a file removed during a session is never recreated by a
later read/write. Startup performs SQLite's `quick_check` and refuses failed
integrity checks without rebuilding, truncating or replacing the file.

The default busy timeout remains three seconds (core callers/tests may choose
zero through 30 seconds). Contention waits occur on background workers. SQLite
serializes independent stores/processes; the per-store lock serializes local
connections. Current bounded reads and serialized writes do not justify enabling
WAL automatically. New stores keep SQLite's default rollback journal; existing
durable journal modes, including WAL, are retained. Connections explicitly use
`synchronous=FULL`. This does not replace the native-operation coordinator.

All failed transaction paths, including validation errors and failed commits,
attempt rollback and close. Cleanup failures produce fixed diagnostic codes,
never raw exceptions or paths. `close()` refuses future access; it does not
delete data. SQLite connection and migration failures become safe `StateError`
messages. A failure to commit the pre-dispatch checkpoint prevents a native
mutation. A failure to commit a verified terminal result preserves the actual
session result, reports a separate persistence warning, and leaves the prior
durable checkpoint for explicit recovery on reopening.

For a backup or manual recovery, quit the desktop first, copy the database to a
private location with any remaining journal/WAL sidecar files, and retain the
original file set until recovery is verified. Do not delete journals to force an
open or copy a live database file alone. Do not open
a newer database with an older app expecting it to migrate backwards. Corrupt
data should be investigated from the backup rather than overwritten silently.
See [database recovery evidence](database-recovery.md) for tested failure cases
and qualification limits.

## Event logs and support exports

`SafeEventLog` records UTC timestamps and validated event/code/operation IDs,
plus optional correlation/execution IDs and a positive event sequence.
It rotates at 64 KiB and retains one backup. It has no free-text message or raw
output parameter. Logging is best effort; log-file errors cannot change native
operation results or print raw filesystem exceptions.
Terminal [application events](application-events.md) use the desktop's existing
serialized state worker. Optional log work yields queue capacity to state/audit
writes. Audit rows still come from the authoritative execution report; the event
bus and its best-effort log are not durable recovery or replay mechanisms.

`services.diagnostics.collect_diagnostics()` builds a bounded report containing
app/Python version, normalized OS/architecture/distribution and backend
availability booleans, plus up to 16 recent registered diagnostic events from a
128-event session ring. Events contain fixed codes/components, optional error
codes/generated operation IDs and UTC timestamps. It excludes executable
locations, exception text, native output, environment values, usernames,
hostnames and audit rows. Export is explicit,
creates a new file without overwriting an existing one, and sends nothing over
the network. Review an exported report before sharing it.
See [runner hardening](runner-hardening.md) for the failure-injection audit and
process-scope guarantees. Raw process output is sensitive even after redaction;
generic credential filters do not make it suitable for diagnostic export.

## Checks

Run against disposable fixtures:

```text
python -m unittest tests.unit.test_persistence tests.unit.test_diagnostics
```

The tests exercise reopen durability, version-1 upgrades, failed-migration
rollback, future-schema preservation, validation, redaction, immutable audit IDs,
bounded logging and opt-in diagnostic export. They never use your real app-data
directory. Packaged clean-machine upgrades remain a separate release gate in
[release.md](release.md).
