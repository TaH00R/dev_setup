# Inventory, planning, and verified operations

Step 6 adds a GUI-independent worker API in root-level `services/`. It does not
change the legacy CLI, install software at import time, or add dependencies.
`models/inventory.py` contains native inventory/review evidence; service review,
consent, and output-free audit records live in `services/records.py`.

## Enabled native inspection

| Inspector | Inventory | Candidate/source review | Mutation review |
| --- | --- | --- | --- |
| `APTInspector` | DPKG status/version rows, architecture aliases | Exact native version, APT metadata and configured Ubuntu noble release origins | Read-only APT simulation of install, selected upgrade, or remove |
| `HomebrewInspector` | `info --json=v2 --installed`, formula kegs and casks | Exact formula/cask token, core/cask tap, stable version and formula revision | Candidate/dependency metadata and explicit native hook caveats; no exact transaction simulation |

WinGet, Scoop, pacman, DNF, zypper, apk, and Flatpak still expose their tested
command contracts. They do **not** yet have inspection implementations for
application orchestration. Selecting one without an inspector returns an
unsupported-operation issue before mutation. Additional native readers and
disposable platform integration are follow-up work; do not wire these command
adapters directly into a GUI install button to bypass the review service.

Native inspection is conservative. Missing managers, failed commands, partial
DPKG installations, duplicate identities, invalid JSON, unknown required formats,
and truncated output fail inspection. They never become “not installed.” Large
inventories exceeding the runner's capture limit currently fail closed rather
than paginate. Unrecognized Homebrew taps are explicitly marked unreviewed.
DPKG does not establish historical download provenance; its source remains
unknown. APT candidate origin checks use native release metadata and do not
replace APT signatures, repository configuration trust, or transport security.

APT uses existing caches and never refreshes repositories. Only candidate
repositories reporting Ubuntu/noble origins are accepted; ambiguous origins
are refused. The reviewed candidate is pinned in install/upgrade arguments.
Simulated `Inst`, `Remv`, and `Conf` effects are visible, including native
dependencies outside the catalog. Purges and unsuccessful simulations are
refused. APT removal retains configuration/data under normal native semantics.

Homebrew preserves the adapter's no-auto-update, no-install-cleanup, and
no-analytics environment. Review shows native dependencies and platform
requirements in metadata, without promising an exact resolved transaction.
Native dependencies, cask installers, service effects and package hooks remain
the package manager's responsibility. No service initialization, PATH edits,
shell changes, or custom environment writes are performed by these services.

## Review and consent API

Construct an inspector for the actual target platform using its matching adapter
and a shared `CommandRunner`. For example, on a supported macOS host, this
example only reads inventory/metadata and creates a review:

```python
from services.catalog.loader import load_catalog
from services.catalog.service import CatalogService
from package_managers.homebrew import Homebrew
from package_managers.inspection import HomebrewInspector
from services.inventory import InventoryService
from services.planning import Planner
from services.records import PackageRequest
from system.platform import detect_platform
from system.runner import CommandRunner

host = detect_platform()
runner = CommandRunner()
catalog = CatalogService(load_catalog())
inspector = HomebrewInspector(Homebrew(host), runner)
observed = InventoryService((inspector,), runner).collect(catalog.packages)
reviewed = Planner(catalog, host, (inspector,)).preview(
    (PackageRequest("git", backend="homebrew", source_id="homebrew-core"),),
    observations=observed.detections,
)
# Render reviewed.plan.items AND reviewed.plan.issues before offering approval.
# This example does not execute the plan or construct consent.
```

`InventoryService` reports executable presence/version output separately from
native product identity. Detection output is bounded and sanitized by the runner;
it is not a universally parsed semantic version. `psql --version` does not prove
a PostgreSQL server is installed or healthy, and `docker --version` does not
identify Docker Desktop. Existing PATH observations can add a warning to a
mutation review, without silently attributing an executable to a native source.

`Planner.preview()` never requests a mutating command from the runner. It resolves
dependencies in topological order, shares common dependencies, rejects cycles,
checks conflicts in both directions, and blocks removal with installed catalog
dependents. Native removal effects also appear in the APT review. A package
requiring multiple source mappings needs an explicit request; there is no source
fallback or tool substitution. Explicit dependency requests can choose their
own source. Exact versions are opaque native strings, not guessed semver ranges.

Already-installed native packages skip installation. A requested version that
differs from an installed version is an issue, rather than an automatic reinstall.
Absent removals and upgrades already at the reviewed candidate skip. Unsupported
capabilities and missing/ambiguous mappings make the entire preview non-executable;
there is no silently executable partial plan after a planning error.

After a real affirmative UI/CLI decision, the caller may construct
`PlanConsent(reviewed, confirmed=True, elevation=<actual elevation decision>)`
and pass it to `OperationService.execute(reviewed, consent=consent, ...)`.
Constructing a plan, receiving this example, or recording a prior approval is
not user consent. Missing, declined, or different-plan consent cancels without
running package commands. Elevation requires its own affirmative decision;
passwords are never collected. Read-only verification and skip-only plans need
no mutation consent. These APIs are internal trusted Python objects, not an
untrusted serialized command/approval API.

## Execution and limits

Before each mutation, execution refreshes installed state, candidate metadata,
and native effects, then rebuilds the adapter command. Changed executables,
arguments, candidate versions, metadata fingerprints, sources, or expanded native
effects require a new review. Native effects may shrink as prior approved items
satisfy dependencies. A package now present at the reviewed native version skips
instead of being installed twice. Native package managers own their transaction
locks; an external process can still change state between preflight and execution.
This service does not claim atomic multi-package transactions or race-free
repository pinning. Homebrew does not support explicit version pinning here.

The service runs synchronously on a caller-owned worker. State observers receive
queued/resolving/consent/installing/verifying/terminal events with operation IDs
and timestamps. The existing state model uses `installing` for native mutations;
the action still distinguishes install, upgrade, and uninstall. Log observers
receive `(operation_id, OutputSnapshot)` replacement snapshots per stream, not
append-only chunks. Observer exceptions are contained. No percentage is invented.

Exit zero starts native inventory verification; it is insufficient for success.
Install/upgrade must show the reviewed native version, and remove must show
absence. Verification does not imply server health, initialized databases,
reboots completed, or verified historical artifact provenance. Nonzero exits,
timeouts and verification failures retain structured errors, bounded redacted
output, exit status and timestamps. No arbitrary exception text is displayed.

Independent items continue after a failure; all transitive dependents skip.
Cancellation stops subsequent work and uses the runner's best-effort native
process termination. Detached or privileged installers may continue, and partial
system changes can remain. No rollback is implemented or claimed.

`ExecutionReport` includes operation results and in-memory audit records with
package/action/backend/source/version, approval time, terminal time and status.
Audit records exclude command output, environment values and credentials.
Persistent history is step 10. The step 7 [desktop UI](desktop.md) now supplies
explicit review/consent and background workers for these services.

Native format references: [Homebrew JSON queries](https://docs.brew.sh/Querying-Brew),
[APT simulation and version selection](https://manpages.ubuntu.com/manpages/noble/man8/apt-get.8.html),
[APT policy queries](https://manpages.ubuntu.com/manpages/noble/man8/apt-cache.8.html),
and [DPKG inventory fields](https://manpages.ubuntu.com/manpages/noble/man1/dpkg-query.1.html).

Tests in `test_services.py` and `test_inspection.py` use fake inventory and a
mocked runner, including an APT preview-to-verification scenario. They do not
run native installs or native inspection commands on the development machine.
Cross-platform native-version compatibility, real consent/elevation prompts and
clean-machine integration remain unverified.

## Step 6 local validation

Windows/CPython 3.14.7 on 2026-11-11:

| Exact command | Result |
| --- | --- |
| `.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -s tests -q` | 227 passed; 50 new service/inspection tests. |
| `.\.venv\Scripts\python.exe -m catalog validate` | 14 packages, 39 reviewed mappings valid. |
| `.\.venv\Scripts\python.exe -m ruff check .` | Passed. |
| `.\.venv\Scripts\python.exe -m ruff format --check .` | 52 files already formatted. |
| `.\.venv\Scripts\python.exe -m mypy` | No issues in 34 source files. |
| `git diff --check` and `git diff --cached --check` | Passed. |

No real manager mutation, native repository search, elevation, or clean-machine
installation was performed. No new dependency was added. Native inspection for
the remaining command adapters, the PySide6 workflow, durable history and
disposable integration coverage remain follow-up work.
