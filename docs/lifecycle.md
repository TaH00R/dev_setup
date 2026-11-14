# Durable operation lifecycle and recovery

Milestone 11 of the expanded roadmap, implemented locally on 2026-11-14.
Native commands, consent and verification still belong to the existing adapters
and operation services. This change introduces no new dependency.

## Durable evidence

`models/lifecycle.py` defines immutable, validated `LifecycleRecord` snapshots.
Each package or runtime execution receives an operation ID and execution ID;
records also retain the reviewed plan ID and a SHA-256 identity of the complete
review. All items, including waiting dependencies, are recorded before work
starts. Evidence includes:

- Package/native/backend/source identity and hashed host identity.
- Hashed review-time installed inventory and requested/candidate version.
  The existing service revalidates native inventory before dispatch.
- Approval, creation, execution-start, update, cancellation-request and finish
  timestamps, when known; an interruption does not invent a completion time.
- Execution phase, known exit code, verification status and bounded detail codes
  for dependency skips, verification failure and possible partial native changes.

Only metadata is serialized. Lifecycle records contain no command arguments,
stdout/stderr, arbitrary error messages, environment values or project contents.
Digest evidence supports comparison without retaining native version output.
The mutation flag records crossing the dispatch checkpoint; after a crash it
cannot prove that the command started or that any particular change occurred.

`LifecycleState` adds `cancel_requested` and `interrupted` alongside execution
phases and per-item outcomes. A request is distinct from confirmed cancellation;
its timestamp survives the final outcome. A requested cancellation may arrive
too late to stop a command that then verifies successfully. Cancellation polls
save requests on the worker thread. A request made immediately before a process
crash may not have reached that poll; the operation is then interrupted/unknown.

`PlanOutcome` is a separate aggregate: running, succeeded, failed, cancelled,
skipped, interrupted or partial_success. A plan with a verified success and a
failed/cancelled/interrupted item has partial_success; the individual outcomes
are retained. Durable outcomes can be derived by grouping lifecycle rows by
execution ID. They are not inferred from the last progress message.

## Checkpoints and failures

`services/lifecycle.py` supplies worker-side checkpoint coordination and a small
store protocol. `OperationService.lifecycle_store` and
`RuntimeService.lifecycle_store` use the desktop's loaded `StateStore`; callers
without a store retain a session-only in-memory journal and retry protection. Startup must finish before the
desktop accepts a package or runtime mutation review.
If persistent startup fails, browsing and session results remain available but
native dispatch stays blocked; it cannot silently switch to unjournaled mutations.

Queued records and the dispatch checkpoint must be committed before native work
starts. A failure at that boundary prevents dispatch. Failures after dispatch
remain separate warnings: they cannot replace the command result or successful
post-operation verification. The last durable phase may then require inspection
on the next startup. Runtime results and package execution reports both expose
safe persistence warnings. Observers and UI log failures remain isolated.

SQLite schema 3 adds the lifecycle table transactionally, preserving v1/v2 audit
and preference data. Saved identity fields cannot change, execution phases cannot
go backwards, and terminal execution outcomes cannot resume. Interrupted records
accept inspection/acknowledgement metadata without rewriting the execution
outcome. Clear History retains entire executions with active or unresolved
interrupted items, including verified predecessors needed to preserve partial
outcomes. It removes fully completed or acknowledged executions.

## Startup and Queue recovery

Startup converts nonterminal records into interrupted records with an unknown
outcome and preserves the last phase and known exit code. It performs no native
inspection, retry, installation, update or removal. Queue opens when unresolved
work is found.

Select an interrupted item and choose **Inspect current native state**. The
worker uses an existing reviewed inspector or runtime adapter. Persisted metadata
cannot define an executable. Inspection distinguishes a matching reviewed target,
a matching previous inventory, changed inventory, and unavailable inspection.
Missing/incompatible backends, host changes and failed reads remain unavailable;
cancellation leaves the prior observation intact.

After a successful inspection, **Acknowledge inspected outcome** requires an
explicit confirmation, defaulting to No. It acknowledges that observation and
closes recovery, without claiming which process changed the system or that the
old command succeeded. The execution outcome stays interrupted. Timed-out or
cancelled mutations that may have started also require inspection; their original
failed/cancelled outcome is preserved. A matching
unfinished tool blocks another mutation until it has been inspected and
acknowledged; any subsequent mutation still needs a fresh reviewed plan.

## Validation and limits

`.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -s tests -t . -q`
passed **437 tests** locally. Ruff lint/format, strict mypy (88 sources), catalog
validation (14 packages/39 mappings) and Git diff checks also passed.

Tests include a real subprocess terminating during a **fake** native mutation,
restart with one verified item and one interrupted item, dependency skips,
cancellation versus confirmed outcomes, exit 0 with failed verification,
dispatch-checkpoint write failure, failure after native success, v1/v2 migration
and migration rollback. GUI checks cover startup, source inspection on a worker,
busy-state gating, cancellation, acknowledgement defaulting to No, and restart
after acknowledgement. No native packages were installed.

Recovery inspects current inventory, not package-manager transaction attribution,
service health or rollback. Project-file changes retain their existing separate
review flow and do not enter this native lifecycle journal. Metadata is retained
until explicit history clearing; lifecycle compaction remains persistence work.
Milestone 12 adds [native access coordination and launch identity](native-safety.md).
Startup now waits for the shared native guard before treating nonterminal rows as
abandoned, so live cooperating instances are protected. Broader database recovery
is milestone 15.
No native clean-machine, elevation or other-OS qualification is claimed here.
