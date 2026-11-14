# Runner failure coverage and safe diagnostics

Expanded roadmap milestone 13, implemented locally on 2026-11-14. No dependency
was added. Native package mutations remain mocked; real process tests create
only disposable Python programs and the existing synthetic Scoop bridge.

## Coverage audit and additions

The audit reused existing contracts instead of recreating them:

| Failure or invariant | Evidence |
| --- | --- |
| Exact adapter argument arrays, source identity, unsupported capabilities and no shell | Existing `test_adapters.py`, `test_adapters_linux.py`, `test_inspection.py`; new common `test_adapter_contracts.py` checks all nine backends, missing managers and query rejection before probing/bootstrap. |
| Valid/invalid domain records, every legal/illegal operation transition, catalog mappings/detection rules and duplicate IDs | Existing domain/catalog suites; a new generated matrix checks 100 launch-hook payload combinations at catalog object boundaries. No property-testing library is required. |
| Zero exit with failed verification, changed inventory/source/candidate after review | Existing `test_services.py`, `test_inspection.py`, `test_lifecycle.py` and `test_launch_identity.py`; retained unchanged where already sufficient. |
| Simultaneous instances, conflicting reads/writes and stale executables | Existing milestone 12 real-kernel-lock and identity tests. |
| Network loss during mutation | Added service injection retains partial native inventory, a failed result and the specific network error; no automatic retry occurs. |
| Full stdout/stderr pipes and backpressure | Existing capture limits plus concurrent 4 MiB output on each stream, a slow observer, bounded queue/capture and coalesced snapshots. |
| Cancellation, timeout, descendants and cleanup | Added real child/grandchild tests, normal parent exit, a termination-ignoring tree, reader setup/read/close failure, unconfirmed cleanup, and Windows suspended-job failure/abrupt owner exit. |
| Final EOF racing with failed output read | A synchronized regression test forces the parent to exit zero just as the last reader fails. The result must remain failed. |
| Application shutdown during execution | A GUI close test cancels a real disposable Python tree and verifies the child has exited before the window is destroyed. Existing settings/history/search shutdown tests remain. |
| Disk-full writes and actual result preservation | Injected SQLite-full error rolls back an audit transaction, preserving prior rows. Injected `ENOSPC` in the GUI keeps the successful session result. Existing pre-dispatch/post-success lifecycle-write failures remain covered. |
| Observer and diagnostic failures | Observer exceptions are isolated; native exit/success is preserved. A failing diagnostic sink cannot change that result. Existing state/event-log failure tests remain. |

## Owned process scopes

`system/processes.py` contains platform-specific ownership and pipe polling;
the generic runner manages bounded output, cancellation, errors and results.

On Windows, the runner creates a suspended process, assigns it to an anonymous
Job Object with kill-on-close, then resumes its initial thread. Failure to create
or assign the job refuses the launch without an uncontained fallback. Ordinary
descendants inherit the job. Cleanup terminates the job and checks its active
process count; normal parent exit cannot leave a background descendant running.
An abrupt runner exit also closes its job handle. The implementation follows
Microsoft's [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects),
[assignment rights](https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject)
and [thread resume](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-resumethread) contracts.

On POSIX, each command starts a new session/process group. Cleanup sends TERM
then KILL to the owned group, even if the parent has already exited, and reaps
the direct child. Descendants that deliberately leave the session/group are
outside this scope. An uncatchable runner crash on POSIX still needs interrupted
operation inspection; it does not gain Windows' kill-on-job-close semantics.

Pipe readers poll for available data instead of blocking indefinitely in a
read. Windows uses [PeekNamedPipe](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-peeknamedpipe);
POSIX uses `select`. Stop requests therefore let readers finish even when another
process retains a pipe. The queue stays bounded, capture continues draining
after truncation, and unchanged full-buffer snapshots are not repeatedly sent.
Cleanup joins readers and closes both pipes and the owned scope. Failed reads
or unconfirmed cleanup produce a structured failure, retain any known native
exit status, and never become success merely because the parent exited zero.
Process-control uncertainty also requires inspection before a matching retry.

This is ownership, not a sandbox or rollback. Detached sessions, service/COM/WMI
brokers, elevation outside the owned scope, access-denied termination and native
package-manager side effects are not universally controlled. Installer/elevation
qualification still belongs in disposable native environments. The final
executable-check-to-spawn race from milestone 12 remains documented.

## Observer isolation

Output/state/log observers are trusted, nonblocking callbacks. After an observer
raises, it is disabled and a fixed diagnostic is recorded; draining, verification
and the real native result continue. The runner's own pipe/monitoring failures
still stop the owned scope and report failure. An arbitrarily blocking Python
callback is not preempted; the desktop's publisher coalesces output and returns
quickly. No third-party callback/plugin execution model is introduced here.

## Safe context and explicit export

`models/diagnostics.py` registers codes/components. `system/diagnostics.py` keeps
at most 128 events in a session-only ring. Events contain only a registered code,
component, optional registered error code, optional generated operation ID and
UTC timestamp. No exception message, traceback, local path, environment value,
native output or command argument is accepted as context.

Startup failures show a fixed `startup_failed` code and direct the user to
catalog validation. When the desktop is available, **Settings > Support > Preview
diagnostics** includes the latest 16 fixed events alongside the existing platform,
version and availability facts. Worker errors point to that route. **Export
reviewed diagnostics** saves that frozen preview to a new file only, with the
existing 16 KiB bound. The schema version remains 1 with an additive
`diagnostics` array. Codes are session-local; a fatal startup dialog's code can
be reported even when the window cannot open. Nothing uploads automatically.

State, lifecycle and optional event-log failures use the same safe codes without
overriding operation results. Diagnostic recording itself is best effort.
Process output remains sensitive after redaction: known-secret and keyword
filters cannot identify every credential. It remains bounded session data,
excluded from audit records and diagnostic export.

## Local validation and limits

- `.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -s tests -t . -q`: **479 tests OK, one POSIX-only shebang test skipped on Windows**.
- `.\.venv\Scripts\python.exe -m ruff check .` and `... -m ruff format --check .`: passed, 155 files.
- `.\.venv\Scripts\python.exe -m mypy`: passed, 94 core source files.
- `.\.venv\Scripts\python.exe -m app catalog validate`: 14 packages/39 mappings valid.
- Documentation links and Git diff checks passed.

Windows owned-tree and GUI tests ran locally. POSIX native execution and the
cross-platform CI matrix await their native hosts. No real packages, repository
refresh, elevation, GPU drivers or system services were changed. Full disk
conditions were injected; the developer's disk was not filled. Milestone 14 adds
typed application events and their Qt bridge; no application event bus is claimed
by this diagnostic ring.
