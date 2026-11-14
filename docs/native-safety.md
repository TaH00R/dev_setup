# Native access coordination and reviewed launch identity

Milestone 12 of the expanded roadmap, implemented locally on 2026-11-14.
No dependency, package-manager bootstrap, execution-policy change or native
installation was needed.

## Native access policy

`system/coordination.py` provides a cancellable, bounded native-access guard.
For now it serializes **all native reads and mutations** for a user, across
backends and runtime managers. This conservative policy avoids incompatible
native transactions, inventory reads during mutations, and lock-order inversions.
It does not advertise independent per-backend concurrency yet.

The default namespace is a private per-user directory under the OS temporary
directory. Independent instances must use the same namespace; tests can inject
an explicit `NativeCoordinator(root, timeout=...)`. Importing modules creates
no lock directory or process. Acquisition checks for symlinks/reparse points,
regular lock files and, on POSIX, private directory ownership/permissions.

A process-local reentrant lock protects threads. Windows uses `msvcrt.locking`
and POSIX uses `fcntl.flock` to exclude other processes. Only the owning thread
may reenter. The file stays in place after release; deleting it could permit
different processes to lock different inodes. Kernel locks release on process
death, with no PID-file deletion or guessed stale-lock timeout. The default
wait limit is ten seconds; waiting can be cancelled. Busy, cancelled and unsafe
path failures have fixed structured errors and do not start a native command.

The public planner, inventory, operation and runtime services hold the guard
around complete workflows, including final verification and lifecycle writes.
The command runner also guards individual launches, covering direct adapter and
live-query callers. Nested calls on the same thread share the guard. Recovery
inspection uses it too. `StateStore.interrupt_active()` acquires it **before**
examining active lifecycle rows: another live instance's mutation is allowed to
finish or recovery fails safely as busy, rather than marking it interrupted.
Persistent startup failure keeps native dispatch blocked.
The preserved legacy menu also guards each installer dispatch and system-info
query, and stops a batch if access cannot be acquired. Its installer internals
remain the existing migration compatibility path; they do not gain the reviewed
identity/lifecycle guarantees of the new operation services.

These are advisory Dev Setup locks. External package managers, detached/orphaned
native processes, different users/namespaces and a hostile actor replacing lock
directories do not participate. Native package-manager locks and subsequent
inventory inspection remain necessary. The guard is not rollback, package-manager
transaction isolation or a guarantee of native process-tree termination.

## Identity bound to review

`system/identity.py` snapshots each reviewed launch file's requested and resolved
paths, file identity, size, modification/change metadata and SHA-256 bytes.
Regular files are bounded to 256 MiB. Windows uses creation metadata where path
and descriptor `ctime` semantics differ, plus identity, modification time and
content digest. Supported POSIX shebang interpreter chains are captured from
the same hashed bytes; simple `/usr/bin/env name` resolution also pins the
selected interpreter. Unsupported interpreter expressions fail closed.

`Command.launch_paths` contains additional **adapter-owned** launch assets.
APT includes `apt-get` behind `pkexec`; Scoop includes both the bundled fixed
`-File` bridge and installed `scoop.ps1`, in addition to PowerShell itself.
No catalog or search-result field supplies these hooks. The Scoop exception
still allows neither `-Command` nor an execution-policy bypass.

The planner/runtime preview binds these snapshots to the immutable reviewed
command. Execution rebuilds and compares the full request, including launch
identity. The runner then checks it again immediately before `Popen`, retaining
argument arrays, `shell=False`, native elevation and exact command approval.
Consent-bearing commands without reviewed identity are rejected. Direct adapter
callers must explicitly bind a command with `system.identity.bind()` as part of
their real review/approval flow; the supported desktop uses the application
services. Launch identity is included in the durable reviewed-plan fingerprint.

Executable replacement, symlink retargeting, changed PATH selection, changed
helpers/scripts or changed file bytes require a fresh plan and approval. File
hashes detect changes; they do not authenticate vendor signatures. This is
revalidation, not atomic execution of an open file handle: a final OS-dependent
check-to-spawn race remains, as do transitive libraries, scripts loaded by a
manager and native elevation daemons. No stronger protection is claimed.

## Retry protection and UI

Interrupted work still requires read-only inspection and acknowledgement. A
timed-out or cancelled mutation that crossed the dispatch checkpoint now also
has `inspection_required` evidence and blocks a matching retry until inspected
and acknowledged. The original failed/cancelled outcome and exit status remain
unchanged. A subsequent attempt still requires fresh native review and consent.

Persistent instances use their lifecycle store; deliberately ephemeral callers
use an in-memory journal for the service/session lifetime. Queue displays these
uncertain results immediately, including in session-only mode. Clearing history
preserves unresolved execution groups. Cancelling a wait or failing to acquire
native access finishes UI queue placeholders instead of leaving them queued.
No waiting task is automatically retried.

## Validation

On the existing Windows development environment:

- `.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -s tests -t . -q`: **456 tests, OK; one POSIX-only shebang test skipped**.
- `.\.venv\Scripts\python.exe -m unittest tests.unit.test_launch_identity tests.unit.test_native_coordination tests.integration.test_native_coordination -q`: **15 tests, OK; the same POSIX-only check skipped**.
- `.\.venv\Scripts\python.exe -m ruff check .` and `... -m ruff format --check .`: passed (150 files).
- `.\.venv\Scripts\python.exe -m mypy`: passed (91 source files).
- `.\.venv\Scripts\python.exe -m app catalog validate`: 14 packages/39 reviewed mappings valid.
- Local documentation links and Git diff checks passed.

Tests exercise distinct service instances, package/runtime conflicts, inventory
exclusion, cancellation while waiting, reentrancy, exception cleanup, unsafe
paths, a real Python process holding the kernel lock, startup recovery while
that process is alive, and lock release after force-killing it. Identity tests
cover same-size replacement with restored modification time, PATH changes,
symlink retargeting, pre-spawn refusal, APT delegation, Scoop's three launch
assets and the unchanged no-shell/no-policy-bypass contract. GUI checks cover
cancelled waits and inspection before retrying a timed-out session operation.

Native mutations are fake. Linux/macOS locking and shebang behavior await native
CI/host execution; no native elevation, package installation or clean-machine
qualification is claimed. Milestone 13 expands runner/process-tree failure
testing and diagnostics.
