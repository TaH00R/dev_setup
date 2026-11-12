# Desktop workflow

Install `requirements-gui.txt` into your virtual environment, then run
`python -m app gui` (or `python main.py gui`). The optional dependency is
`PySide6-Essentials`, which supplies Qt Widgets and QtTest without the browser,
PDF, or other addon modules. Catalog commands and all core services still work
without importing Qt. A missing dependency produces installation instructions.

This is the first desktop version, with staged backend support. Browsing works
on supported Windows, Linux and macOS platforms. Native package management uses
the existing APT/Ubuntu 24.04 and Homebrew inspection services. Other adapters,
including WinGet and Scoop, remain unavailable for desktop execution until their
native readers are reviewed. The UI explains this rather than guessing support.

## Screens

- **Dashboard** shows the detected OS/architecture, available package managers,
  native inventory count, limitations and recent activity.
- **Explore** combines curated-first metadata with asynchronous live search,
  configurable debounce, category and curated-platform filters. Package details include provenance, native
  ID, homepage, license, dependencies, size when known, privileges and restart
  notes. Source and action choices produce a fresh service plan. Exact native
  version input is available only when supported; it is not a fetched version list.
  Live hits are marked unreviewed, retain native/source identity, and cannot start
  an installation. Provider failures retain partial results. See [discovery](discovery.md).
- **Installed** shows native package identity, versions, backend and recorded
  source. Unknown origin stays unknown. Unmapped native tools remain visible but
  do not receive guessed catalog mappings or install actions. Executable probes
  are separate observations, not proof of product identity or server health.
- **Updates** checks mapped installed catalog tools read-only. Native candidate
  equality uses opaque version strings, not guessed ordering. Failures and
  unavailable inventory appear as limitations. Selecting an update creates a
  new plan for review; checking never installs anything.
- **Queue** shows active and waiting plan items, terminal results, exit status,
  structured errors and bounded stdout/stderr. Native output remains plain text.
- **History** keeps up to 200 output-free audit records for the current session.
- **Settings** changes the light/dark theme and search delay in memory, lists
  reviewed sources, and can clear session history/logs. Native repository changes,
  persistent preferences and configurable concurrency are not implemented.

## Review and approval

The review dialog displays the exact argument arrays from the prepared plan,
source/trust metadata, native candidate/installed versions, dependency order,
expected native changes, size, restart notes and actual privilege requirements.
Planning issues disable execution. Mutations require an initially unchecked
operation approval box. Native elevation needs a separate initially unchecked
decision. Cancel is the default dialog action. Closing or rejecting a review
never starts an installer; only accepting an eligible review creates `PlanConsent`
for that exact immutable plan. Read-only verification and skip-only plans need no
mutation consent. The core revalidates native evidence before execution.

Homebrew review still describes native dependency metadata rather than an exact
transaction simulation. APT uses read-only native simulation. The GUI preserves
the service's source, version, verification and partial-support limitations; see
[operation services](operations.md).

## Workers, cancellation, and privacy

`services/desktop.py` aggregates native inventory and candidate checks without Qt.
`app/bootstrap.py` selects existing reviewed inspectors. Widgets render those
records and dispatch user intent through `app/ui/workers.py`. One QThread job per
window runs inventory reads, update checks, plan preparation and execution.
Signals deliver progress, replacement log snapshots and results to the GUI thread.
Log notifications are coalesced so noisy native output cannot create an
unbounded backlog of large Qt events.
The core imports no Qt, app, or legacy installer modules.

One plan runs at a time. Cancellation sets a thread-safe event passed through to
the runner; it does not terminate a QThread. Closing the window requests
cancellation and waits for the worker to finish before destroying it. External
application shutdown also keeps the controller alive until the worker stops.
Native detached/privileged descendants may continue; no rollback is promised.
Native process creation can delay cancellation on some systems.

Queue display and logs retain at most 100 operations. Each stream retains the
runner's bounded, sanitized snapshot (up to 65,536 characters plus a truncation
marker). Audit records exclude output and environment values. Raw exceptions
are not shown. Settings/history/logs are not persisted, and no application
telemetry is collected. SQLite history and settings migration remain step 10.

## Verification

`tests/gui/test_desktop.py` uses QtTest and an offscreen Qt event loop with mocked
native operations. It covers navigation, filters/details, source limits, consent,
elevation, approval/rejection, background responsiveness, queued dependencies,
logs, failures, verification, update review, cancellation, shutdown and settings.
Qt-independent desktop aggregation tests cover mapping/provenance, candidate
selection, missing backends, cancellation and incomplete inventory.

Run the full suite with:

```sh
python -W error::ResourceWarning -m unittest discover -s tests -t . -q
```

The optional GUI suite explicitly skips if Qt is absent. CI installs the GUI
dependency and sets `QT_QPA_PLATFORM=offscreen` on Windows, macOS and Linux.
Core import and catalog launcher tests still run in isolated Python interpreters
without site packages, and forbid Qt/legacy imports or native processes.

Window renders were inspected locally using offscreen Qt with system fonts for
Dashboard, Explore and review. No screenshots, local paths or generated assets
are committed. Real native installs, graphical privilege prompts, clean-machine
GUI integration, desktop packaging and remote CI remain unverified locally.

Qt lifecycle and testing references:
[QThread](https://doc.qt.io/qtforpython-6/PySide6/QtCore/QThread.html) and
[QtTest](https://doc.qt.io/qtforpython-6/PySide6/QtTest/QTest.html).

## Step 7 local validation

Windows/CPython 3.14.7 with PySide6-Essentials 6.12.0, 2026-11-11:

| Exact command | Result |
| --- | --- |
| `.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -s tests -t . -q` | 266 passed, including all 19 GUI tests; no GUI skips locally. |
| `.\.venv\Scripts\python.exe -m app catalog validate` | 14 packages, 39 reviewed mappings valid. |
| `.\.venv\Scripts\python.exe -m ruff check .` | Passed. |
| `.\.venv\Scripts\python.exe -m ruff format --check .` | 80 files already formatted. |
| `.\.venv\Scripts\python.exe -m mypy` | No issues in 43 source files. |
| `git diff --check` and `git diff --cached --check` | Passed. |

The desktop bootstrap also opened and exited with status 0 under offscreen Qt
using a timed application quit. Local documentation links have no missing
targets. Native developer-tool installs, real administrator prompts, packaged
desktop executables, and remote OS CI were not run.

## Presets and runtime reviews

Presets contains the ten starter roles, each with an editable list of reviewed
catalog tools. These are starter collections, not promises to install a complete
language stack. Save a named collection for the current session, or export a JSON
manifest to keep it. Export requires a new file and refuses to overwrite existing
files. Import validates the manifest through the core service; unknown package
IDs remain visible so they can be resolved explicitly.

Preview resolves the selected packages for the current OS, architecture and
backend. It lists unsupported items, source identifiers and exact arguments.
Unsupported items block package execution. When the preview is ready, **Review
package operations** opens the existing package consent dialog. Editing the
collection invalidates its previous preview. Runtime and project configuration
choices travel with the manifest and require their own reviews on Runtimes.

Runtimes supports exact versions through an already installed mise (Python,
Node.js and Go), uv (Python), or rustup (Rust). Native discovery, installation and
verification run in background workers. The review shows the actual arguments,
source, privileges and manager caveats before consent. Runtime managers are not
automatically installed. Java via mise, SDKMAN!, Conda and Mamba remain staged.

Choose an existing project directory to review `.python-version`,
`rust-toolchain.toml`, or an owned minimal `mise.toml`. Each file review shows
current and proposed content. Only the exact approved plan can be applied; the
core rejects stale files and unmanaged TOML. The optional `.dev-setup.env` contains
only reviewed project-relative path variables and is not loaded automatically.
These controls do not change global PATH or shell startup files.

Package and runtime operations reserve a shared execution gate through consent
and execution. Cancellation is best effort; closing the window waits for workers
to finish rather than destroying a running thread.

Local validation on Windows, 2026-11-12:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.gui.test_environments tests.gui.test_desktop tests.gui.test_live_search -q
```

All 37 tests passed, including nine environment flows using mocked native work
and temporary project files. Offscreen Presets and Runtimes renders were inspected
at 1200×800 with application-loaded system fonts; no generated images were added
to the repository. Real native runtime installations and other OS window systems
remain unverified locally.
