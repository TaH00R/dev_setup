# Local settings, history and diagnostics

Dev Setup keeps desktop preferences and completed operation metadata in an
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
| `theme` | `light` | `light`, `dark` |
| `search_delay_ms` | `250` | integer from 150 through 1000 |
| `live_search` | `True` | an actual boolean |

`load_settings()` returns defaults when no preference row exists.
`save_settings(settings)` changes only the validated preference row. Invalid saved
data produces a safe `StateError`; callers can show defaults without overwriting
the database. The desktop performs state I/O on its state worker, rather than
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
`clear_history()` deletes audit records while retaining preferences.

The database is an operation history, not a durable execution queue. A process
crash before a completed report is saved can leave a history gap. A persistence
failure must be shown separately from the actual native operation result; it does
not turn a successful installation into a failed installation or imply rollback.

## Schema upgrades and recovery

The SQLite `user_version` is currently 2. Version 1 contains audit history; version
2 adds the singleton settings table. A new database creates both. Migrations use
an explicit transaction and preserve history. A failed migration rolls back both
schema changes and the version number. A database from a newer version, an
unrecognized schema, or an existing unversioned database is refused without
deleting its tables. There is no automatic destructive downgrade or reset.

Connections are short lived and serialized per store. `close()` refuses future
access; it does not delete data. SQLite connection and migration failures become
safe `StateError` messages without raw paths or database exception details.

For a backup or manual recovery, quit the desktop first, copy the database to a
private location, and retain the original until recovery is verified. Do not open
a newer database with an older app expecting it to migrate backwards. Corrupt
data should be investigated from the backup rather than overwritten silently.

## Event logs and support exports

`SafeEventLog` records UTC timestamps and validated event/code/operation IDs only.
It rotates at 64 KiB and retains one backup. It has no free-text message or raw
output parameter. Logging is best effort; log-file errors cannot change native
operation results or print raw filesystem exceptions.

`services.diagnostics.collect_diagnostics()` builds a bounded report containing
app/Python version, normalized OS/architecture/distribution and backend
availability booleans. It excludes executable locations, native failure reasons,
environment values, usernames, hostnames and audit content. Export is explicit,
creates a new file without overwriting an existing one, and sends nothing over
the network. Review an exported report before sharing it.

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
