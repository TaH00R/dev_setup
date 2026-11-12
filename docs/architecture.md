# Architecture and boundaries

The new application will use the following dependency direction:

```text
PySide6 widgets and Qt workers
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
planning, exact-plan consent and verified operations. Step 7 adds the optional
PySide6 presentation layer in `app/ui/` and Qt-independent desktop aggregation
in `services/desktop.py`.
Step 8 adds Qt-independent discovery records/services and adapter-owned parsers,
with an independent generation-aware search worker in `app/ui/search.py`.
Importing core modules must not
discover software, start a process,
load Qt, or import `app`, the launcher, or `app/legacy/`.

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
Persistent settings and sanitized history belong behind core persistence
interfaces, not widget code. Runtime managers form a separate adapter family
because project-local runtimes differ from system application installation.

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
