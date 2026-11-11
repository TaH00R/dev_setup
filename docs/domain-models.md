# Core domain records

The new core uses standard-library frozen dataclasses and enums. Constructors
validate domain invariants and accept immutable tuples, not mutable lists. A
future catalog loader will decode declarative data into these types and reject
unknown fields. This step does not introduce that loader or catalog files.

Core modules live in `models/` and `system/` directly under the repository root.
There is no nested `dev_setup/` package. Import paths are `models.platform`,
`models.package`, `models.specs`, and `system.platform`.

## Platform identity

`Platform` holds `OperatingSystem`, `Architecture`, and optional
`LinuxDistribution` (exact ID, family, ordered ID_LIKE ancestry, version, name).
`detect_platform()` inspects the host only when called; explicit OS, machine,
and os-release facts permit deterministic tests and previews for other machines.
No command, network request, or third-party distro dependency is involved.
Unknown systems, architectures, and missing/unknown distro families have an
`unsupported_reason`. This is an identity diagnostic, not proof of backend or
package availability. Actual capability/availability checks come later.

`PlatformSupport` explicitly lists architectures and, for Linux, reviewed exact
distribution IDs and optional exact distribution-version restrictions. Missing
release facts do not match a release-restricted mapping. Matching never expands an Ubuntu mapping to Debian just
because they share ancestry. Distribution-family detection provides context;
it does not infer package IDs or install a package manager.

## Package definitions

| Record | Fields and rules |
| --- | --- |
| `Package` | Stable lowercase ID, name, summary, category, HTTPS homepage; optional tags, docs, license, vendor, install/detection specs, dependencies, conflicts, and environment guidance. Empty install mappings represent no reviewed installation path. |
| `PackageSource` | Stable source ID/name, curated or live kind, explicit official/community/vendor/unknown trust, optional credential-free HTTPS URL. Curated origin does not imply official-repository trust. |
| `PackageVersion` | Opaque native version string and optional channel. No universal semantic-version ordering or comparison is assumed. Backend capabilities must validate selection. |
| `Dependency` | Stable package ID and optional typed version. The package aggregate rejects duplicate references, self-dependencies, self-conflicts, and dependency/conflict contradictions. Cross-package references and cycles need catalog/planning services. |
| `InstallSpec` | Explicit support, backend, exact native ID, provenance, optional version, privilege requirement, byte estimate, restart flag, and notes. Generic ID syntax validation is followed by backend-specific validation in future adapters. |
| `DetectionSpec` | Bare executable name, one allowlisted version flag or a reviewed version subcommand, bounded timeout, optional platform support. No paths, interpreter scripts, arbitrary arguments, or shell hooks. |

Text fields reject control characters and outer whitespace. Human-readable
guidance and notes are display-only data, never commands. URLs identify metadata;
they are not authorization for a vendor download. A direct-download adapter will
need trusted-origin and signature/checksum verification independently.

All examples in domain tests use synthetic package/source IDs. They do not add
catalog entries or assert that any native package exists in a registry.

`InstallSpec.review` optionally records a `MappingReview` (credential-free HTTPS
evidence URL and explicit review date). The curated catalog loader requires this
evidence for every shipped mapping; it records a source metadata review rather
than an actual installation test. Native adapter capability checks remain separate.

## Reviewable plans and errors

`InstallPlan` captures a target platform, unique dependency-ordered `PlanItem`
records, creation time, and any structured issues. Unsupported packages are
reported as issues rather than fabricated install specs. An empty plan or one
with issues is not ready. Skipped items have a reason and no command previews.
`OperationAction` distinguishes installation, removal, upgrade, and verification.

`CommandPreview` contains adapter-supplied redacted display arguments and a
description. It is display metadata, never executor input or catalog content.
`PlanItem` retains selected source/backend/version through its install spec,
declared dependencies, system-change descriptions, and consent requirements.
Creating records neither executes commands nor grants consent. Services and
adapters still need to verify availability, capabilities, permissions, and actual
user approval. Privilege consent must also be enforced by future services.

`OperationError` records an explicit `ErrorCode`, safe message, retryable flag,
and sanitized details. Codes distinguish unsupported platforms, missing backend,
network/timeout/permission problems, command failures, dependency cycles or
conflicts, version mismatch, cancellation, and failed verification. These values
describe errors; they do not automatically redact secrets or perform recovery.

## Operation lifecycle and progress

`Operation` is an immutable snapshot referencing its reviewed `PlanItem` so
package ID, source, backend, version, command previews, and privileges remain
available. It records creation/update/start/consent timestamps, optional progress,
and a terminal `OperationResult`. `advance()` returns a new snapshot and rejects
backwards timestamps, terminal-state exits, inconsistent results, and illegal
transitions. All timestamps are timezone-aware; defaults use UTC.

```text
queued -> resolving -> awaiting_consent -> downloading -> installing
                                                        -> verifying -> succeeded
```

Downloading is optional. Mutations cannot bypass consent or verification;
leaving the consent state for execution records explicit approval. A read-only
verify action may go from resolving to verifying. Resolving can skip an
already-installed item. Queued/active work can cancel, active work can fail,
and every terminal state requires a human-readable result. Skipped plan items
cannot execute, and verification actions cannot enter installation states.
Constructor invariants also check snapshots restored by future persistence.

These records do not collect consent themselves: an application service must
obtain actual approval of the current plan and enforce elevation separately.
They do not implement the runner, dependency resolver, verification probes,
rollback, cancellation of a real process, or inventory services.

`OperationResult` holds terminal state, message, exit status, structured failure,
bounded stdout/stderr, explicit output-truncation status, and finish time. Failed
results need an error; a non-zero exit cannot become success. Verification may
fail even with exit code zero. Skipped results cannot claim command execution.
Each output stream is capped at 65,536 characters and excluded from `repr`;
the future runner must bound capture and sanitize sensitive output before
constructing/persisting records. This is not automatic secret redaction.

`ProgressEvent` carries operation/package IDs, state, safe message, timestamp,
and optional percentage. Percentages are finite numbers in [0, 100]; `None`
means the backend has not reported measurable progress. Percentages are per
stage, not an invented estimate for the entire installation.

## Step 2 scope

Step 6 extends the records with native inventory, detection observations, and
candidate review evidence in `models/inventory.py`. A read-only `VERIFY` plan
item may have no executable preview: verification reads native inventory instead
of inventing a package mutation command. Non-skipped mutations still require
command previews. Service review/consent/audit records and execution behavior
are described in [operation services](operations.md).

Implemented: typed/validated records, pure platform normalization, explicit host
inspection, immutable plans, lifecycle validation, structured errors, results,
and progress events. No additional dependencies were introduced. The legacy CLI
continues using its existing modules. The next step adds catalog loading,
validation, category browsing, search, and verified initial package mappings.

Validation on Windows/CPython 3.14.7, 2026-11-11:

| Command | Result |
| --- | --- |
| `.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -s tests -v` | 111 tests passed, including 42 domain tests and the expanded isolated import check. |
| `.\.venv\Scripts\python.exe -m ruff check .` | All checks passed. |
| `.\.venv\Scripts\python.exe -m ruff format --check .` | 19 files already formatted. |
| `.\.venv\Scripts\python.exe -m mypy` | No issues in 11 core source files. |
| `git diff --check` | Passed. |
| `Test-Path -LiteralPath '.\dev_setup'` | False: the nested package directory is removed. |

OS/distro tests inject explicit facts; no package operations or registry requests
are performed by the new tests. Remote cross-platform CI and actual package
installation remain unverified in this local run.
