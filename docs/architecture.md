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
platform inspection are being introduced in step 2; application services,
adapters, and the GUI remain future work. Importing core modules must not discover software, start a process,
load Qt, or import `main.py`, `system_info.py`, or `installers`.

Widgets render state and send user intent to services. Services build plans and
coordinate operations against explicit adapter capabilities. Adapters validate
native identifiers/options and construct argument arrays. The runner will use
`shell=False`, bounded capture, timeouts, cancellation, and structured errors.
Neither services nor widgets should build package-manager shell commands.

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

Legacy code retains its existing entry point during migration. Its procedural
installation paths are not the template for new adapters. In particular,
direct downloads, implicit system refresh/upgrade actions, PATH modification,
unverified package mappings, and optimistic success messages require explicit
replacement and regression coverage before the new application uses them.
