# Architecture and boundaries

The new application will use the following dependency direction:

```text
Textual terminal widgets and cancellable workers
          |
Application services (catalog, discovery, planning, inventory, operations)
          |
Typed domain models and adapter contracts
          |
Package-manager / runtime / OS adapters
          |
Operating system through a shared safe command runner
```

The UI-independent core lives in `models/` and `system/` directly under the
repository root, without a nested `dev_setup/` directory. Domain records and
platform inspection were introduced in step 2. Step 4 adds reviewed native
adapters in root-level `package_managers/` and the worker-side command runner
in `system/`. Step 6 adds root-level `services/` for inventory, dependency/conflict
planning, exact-plan consent and verified operations. The 2026-11-18 terminal
pivot replaces the Qt interface with `app/tui/`: pages, plain-text rendering,
explicit review screens, keyboard actions and cancellable background work.
`application/session.py` owns persistent preferences, audit and lifecycle wiring;
`application/environments.py` provides portable file workflows. Native reads,
writes and state I/O execute on workers; widgets only render cached state.

`services/desktop.py` retains its existing UI-independent API names for core and
native-profile compatibility. Discovery, runtime and environment services remain
framework-independent. Importing core modules must not load Textual/Rich, import
`app`/the legacy launcher, start native processes or make network requests.

Standalone bundles include the interpreter, terminal library/assets, catalog,
SQLite support and reviewed Scoop bridge. `scripts/tui_entry.py` is the console
entry point; `scripts/build_tui.py` builds OS-specific artifacts and verifies them
from a foreign directory with PATH empty and Python overrides removed. Qt and the
legacy installers are excluded. End users do not install Python or pip.

The expanded roadmap's milestone 12 adds `system/coordination.py` for native
access serialization across services/processes and `system/identity.py` for
reviewed executable/interpreter/helper snapshots. Planning, inventory, execution,
verification and startup recovery cooperate through the same guard. The runner
revalidates launch identity against its captured process environment immediately
before dispatch. See [native safety](native-safety.md) for policy and limits.
Milestone 13 adds `system/processes.py` for owned OS process scopes and
`models/diagnostics.py`/`system/diagnostics.py` for fixed session diagnostic
context. Diagnostics are independent of UI libraries and exclude native output; support
preview/export remains explicit. See [runner hardening](runner-hardening.md).

`catalog/` contains only reviewed JSON package definitions. Python loading,
validation, and browsing/search live in `services/catalog/`, sharing `models/`
and does not invoke package managers or query registries. Catalog matching
describes reviewed platform mappings. Adapters now expose availability and
consent-bound native requests. APT and Homebrew have native inspection in
`package_managers/inspection.py`; additional adapters fail orchestration explicitly
until their inventory and transaction formats have reviewed parsers. The shared
evidence records live in `models/inventory.py`, so adapters do not depend on
application services. See [operation services](operations.md) for the API and limits.

Widgets render state and send user intent to services. Services build plans and
coordinate operations against explicit adapter capabilities. Adapters validate
native identifiers/options and construct argument arrays. The runner uses
`shell=False`, bounded capture, timeouts, cancellation, and structured errors.
Neither services nor widgets should build package-manager shell commands.

Terminal page composition lives in `app/tui/pages.py`; `actions.py` routes intent,
`review.py` enforces explicit consent and elevation, and `presentation.py` renders
reviewed domain records as plain text. `app.py` manages navigation, worker
lifetimes, generation-aware search and bounded log snapshots. Shutdown cancels
and waits for owned work. Terminal metadata cannot inject styling or control
sequences. See [terminal workflows](tui.md).

The user-authorized fixed Scoop `-File` bridge is documented in
[package-manager boundaries](package-managers.md); it is not a general script API.

Catalog data describes packages, exact platform mappings, detection arguments,
dependencies, trust, and compatibility. It must never supply executable hooks.
Live results retain source plus native package ID and a distinct verification
level. OS and Linux distro detection selects compatible adapters, rather than
assuming every Linux package manager or native identifier is interchangeable.

Plans expose package/source/backend, requested versions, dependencies, exact
commands, elevation, and system changes before execution. Consent must be tied
to the reviewed plan. Operations expose explicit states, timestamps, output,
exit codes, and verification results. Unsupported operations and partial
failures must remain visible; cancellation does not imply rollback.

Qt workers keep blocking tasks off the GUI thread and relay events safely.
Typed metadata in `models/events.py` flows through the Qt-independent
`services/events.py` bus and bounded `app/ui/events.py` signal bridge. Package
success events follow native verification; raw output uses its separate channel.
See [application events](application-events.md) for ordering, ownership and limits.
Persistent settings and sanitized history belong behind core persistence
interfaces, not widget code. Runtime managers form a separate adapter family
because project-local runtimes differ from system application installation.
Database work runs through a dedicated Qt worker. Persisted history contains
operation metadata rather than process output or environment values. Startup
failures retain the existing database and allow the session to continue.
Lifecycle checkpoints also use the store from native-operation workers, committing
before command dispatch. `models/lifecycle.py` separates durable cancellation,
interruption and inspection evidence from legacy per-item execution states and
defines aggregate plan outcomes. `services/lifecycle.py` coordinates these
checkpoints and read-only native recovery. Startup and Queue never replay a
stored command; lifecycle rows contain no command strings or raw output.
Schema 4 adds reviewed catalog content references. The SQLite store owns read
snapshots, immediate write transactions, version/integrity checks and safe
rollback/cleanup. GUI state saves and lifecycle workers share that store without
sharing connections; see [database recovery](database-recovery.md).
Release verification never executes a downloaded artifact; a build without a
pinned release channel makes no update request.

`app/main.py` exposes read-only catalog commands through `python -m app` and
the small root `main.py` launcher. `python -m app gui` lazily imports the PySide6
bootstrap, with screens, review and workers under `app/ui/`. The old menu,
system information helper and installers live together under `app/legacy/`.
`python -m app legacy` opts into that menu with lazy imports, preserving its
regression coverage while keeping its dependencies out of core startup.

The legacy procedural
installation paths are not the template for new adapters. In particular,
direct downloads, implicit system refresh/upgrade actions, PATH modification,
unverified package mappings, and optimistic success messages require explicit
replacement and regression coverage before the new application uses them.
