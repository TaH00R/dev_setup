# Application events

Milestone 14 adds an in-process metadata stream for package operations. It does
not change consent, native command construction, verification or lifecycle
checkpoints. No dependency was added.

## Core contract

`models/events.py` defines frozen `OperationChanged` and `OperationFinished`
records, with terminal specializations `PackageInstalled`, `PackageRemoved`,
`PackageUpgraded` and `OperationFailed`. Every record carries the reviewed plan's
`correlation_id`, one execution's `execution_id`, and the individual
`operation_id`. Re-executing a plan retains correlation and generates new
execution/operation IDs. The bus adds a monotonically increasing sequence number
for that bus instance. Timestamps describe the operation, not delivery time.

Payloads contain package/native/source/backend identity, action, state and a
registered error code when available. Finished events add version, approval time
and exit status. No message, command, environment, traceback, stdout or stderr
field exists. These records are reviewed application metadata; they are not a
way to execute untrusted plugins or remote descriptions.

`OperationService` emits changed events after its lifecycle checkpoint and a
finished event after constructing the authoritative audit row. Package success
events are emitted only after the existing post-operation native inventory
verification succeeds. Exit zero alone emits no package-success event. Verify,
skip and cancellation use `OperationFinished`; they never imply installation.
Failed verification produces `OperationFailed`, retaining the native exit code
and a structured error code. Raw output stays in the separate bounded session
output channel and execution report.

`services/events.py` owns a small synchronous `EventBus`:

- Concurrent publishers serialize, and subscribers run in subscription order on
  the publishing thread. Callbacks must return promptly.
- Nested publication queues behind the current event, so subscribers see the
  same order. Subscriptions added during a delivery start with the next event;
  unsubscribed callbacks are skipped even during the current delivery.
- A callback exception removes that subscriber, records a fixed diagnostic, and
  continues other subscribers. The operation service isolates metadata projection
  failures too, preserving the native result.
- Subscription handles close idempotently and support context managers. Owners
  must close them. There is no global bus or retained event history.
- Limits are 64 subscribers, 256 queued nested publications, and 1,024 deliveries
  per outer publication to stop reentrant feedback. A nested publication rejected
  at capacity returns `False`; discarded feedback records a fixed diagnostic.

`DesktopServices.events` is shared with its package operation service. The
existing progress observer remains supported for non-desktop callers. Runtime
and project-file workflows retain their current audit/signals in this incremental
step; they do not emit package-success events. This is not a durable event journal
or distributed messaging system. Lifecycle recovery remains authoritative after
a crash; events are never replayed into commands.

## Qt boundary and local logging

`app/ui/events.py` subscribes through an `EventBridge`. A single queued wake drains
metadata on the bridge's Qt owner thread. It retains at most 512 pending records;
overflow discards oldest records and emits an omission count. Retained records
stay ordered even through nested Qt event loops. Explicit close and QObject
destruction unsubscribe. Close discards queued delivery. Before handling the
final execution report, the GUI drains pending metadata once; final reports
restore full Queue details and History if live updates were omitted.

Queue state, History, Dashboard activity and status notices use this shared
stream. History merges by operation ID, so final reports do not duplicate rows.
Verified package mutations mark Dashboard/Installed inventory as stale and clear
old update candidates. Refresh performs the existing read-only worker inventory
query; the UI does not invent installed versions from a success notification.
An update check requires refreshed inventory. Overflow also marks inventory stale.

When persistent state is enabled, terminal event names, sequence, correlation,
execution and operation IDs are written through the existing serialized state
worker to `SafeEventLog`. The log still rotates at 64 KiB with one backup. Its
optional queue sheds metadata at saturation before occupying all state-write
slots; failures cannot change a native result. It is a best-effort local log,
not a durable event replay source. Database audit rows continue to be saved from
the final execution report. Diagnostic preview/export still excludes these
application-event/audit rows and raw output, requires explicit user action, and
uploads nothing. See [persistence](persistence.md).

## Validation

Unit tests cover immutable payloads, registered types, consistent terminal
outcomes, concurrent and nested ordering, cleanup during delivery, callback
failure isolation, late subscriptions and queue/feedback bounds. Service tests
cover verified install/update/remove, plain verification, skip, repeated-plan
correlation, and exit-zero verification failure. GUI tests cover thread delivery,
owner destruction, close, overflow, reentrant delivery, final-report draining,
shared-view invalidation and correlated metadata logging without raw output.

Run the full deterministic suite:

```powershell
.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -s tests -t . -q
```

Native package qualification and stronger database recovery remain separate
roadmap milestones. This work performs no package installation or network request.

Local Windows validation on 2026-11-15: the full command above ran **504 tests,
OK with one POSIX-only skip**. Ruff lint and format checks passed (160 files),
mypy passed (97 source files), and catalog validation passed (14 packages,
39 reviewed mappings). Native POSIX, elevated and clean-machine qualification
remain separate release gates.
