# Database recovery evidence

Milestone 15 extends the existing store and lifecycle journal; it adds no
dependency, database server, background replay or automatic repair operation.

## Implementation

- Schema 4 converts v3 lifecycle JSON transactionally and records a nullable
  SHA-256 catalog revision reference. v1/v2 migrations still preserve preferences
  and audit history. Existing unknown catalog references stay unknown.
- Catalog revisions are derived from normalized reviewed metadata. Plans retain
  their revision, which also participates in exact-plan consent/identity. It
  cannot change on a saved lifecycle record, including during reconciliation.
- Every connection uses an explicit transaction. Version inspection occurs
  inside the snapshot/write reservation, including during migration. All paths
  close connections and roll back uncommitted work; cleanup diagnostics omit
  exception text. Read/write metadata validation cannot partially clear history.
- Startup uses `quick_check`, validates expected table columns, and refuses
  future or incompatible schemas. Saved lifecycle keys must match their payloads.
  The store never replaces a corrupt database or recreates a missing live file.
- The default three-second busy timeout and short-lived, per-store serialized
  connections remain. New files retain the default rollback journal; existing
  WAL databases are accepted without changing their mode. Each connection uses
  `synchronous=FULL`. Tests with independent stores demonstrate serialized writes
  and reads of committed snapshots during reserved-writer contention. Automatic
  WAL adds no necessary capability for the current workload, so it stays off.

The existing Qt state worker serializes preference/audit writes, while lifecycle
checkpoints run on the native-operation worker before command dispatch. SQLite
coordinates these separate connections. The native-access guard remains required
for manager operations and startup interruption detection; a database write lock
alone does not coordinate native installations.

## Evidence matrix

| Boundary | Disposable test evidence |
| --- | --- |
| v1/v2 history/settings upgrades | Existing migration tests retained and updated for schema 4; prior preferences and audit records survive |
| v3 lifecycle format upgrade | Active/queued evidence, dispatch flag and timestamps retained; historical catalog revision stays null |
| Bad lifecycle JSON or mismatched key | Safe refusal; no raw value in errors; clear/write cannot erase prior audit evidence |
| Failed migration commit | Injected SQLite commit failure preserves old JSON and version; next normal open succeeds |
| Simultaneous migration | Six independent stores open the same old database and retain the same evidence |
| Independent writers | Four stores concurrently write 48 audit rows plus settings/lifecycle snapshots; all prior data survives |
| Lock timeout | Real reserved write lock allows committed reads; timed-out writes/open retain data and work after release |
| Qt responsiveness | A timer fires while the state worker waits on a real SQLite write reservation; write succeeds after release |
| Full database | SQLite `max_page_count` forces a real full-database failure during a batch; prior audit/settings/lifecycle survive reopen |
| Commit before native dispatch | Injected SQLite commit failure prevents the fake native runner from mutating |
| Commit after native verification | Verified session result remains succeeded with exit zero; separate warning and last durable verifying phase survive |
| Rollback failure | Injected rollback error is sanitized; closing the real connection rolls back prior partial writes |
| Abrupt process exit during write | A disposable Python process changes settings/lifecycle and spills uncommitted pages, then exits without cleanup; SQLite rolls back on reopen |
| Abrupt process exit during migration | Process exits after payload/version changes but before commit; original v3 state survives, then migrates on normal open |
| Existing WAL mode | Mode retained, FULL synchronous setting applied, evidence survives reopen |
| Corrupt/missing/future database | Original corrupt bytes retained; missing live file not recreated; an already-open store refuses a future schema without writing |
| Catalog reference | Metadata changes alter the digest; package-file ordering and fresh interpreters with different hash seeds do not |

All native operations in database tests are fake. Test files, page limits,
connections and subprocesses belong to temporary directories; the developer's
disk is never filled. Existing log-failure, safe diagnostics, startup recovery,
history retention and explicit inspection/reconciliation tests remain in place.

Run the deterministic checks:

```powershell
.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -s tests -t . -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m app catalog validate
```

Local Windows results on 2026-11-15: **524 tests OK, with one POSIX-only skip**;
Ruff lint/format passed (162 files), mypy passed (97 source files), and catalog
validation passed (14 packages/39 reviewed mappings). Local links in 20 Markdown
documents and Git diff checks passed.

Process crashes and page-limit faults do not qualify power-loss recovery,
filesystem/controller durability, network-mounted databases or clean-machine
upgrades on other operating systems. Corruption is safely refused, not repaired.
Catalog references are not archived catalog snapshots. No automatic retry,
rollback of native changes, destructive recovery, checkpoint-file deletion or
publication is introduced. Native APT/Homebrew qualification is milestone 16.
