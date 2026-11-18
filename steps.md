# Dev Setup — terminal application roadmap

Updated 2026-11-18 following the product change from a Qt GUI to a terminal UI.
The existing core, catalog, adapters, persistence, recovery and native evidence
remain the foundation. The terminal app uses these services directly.

## Distribution requirement

**Users must not need Python or pip installed.** Ship a native executable bundle
for each supported OS/architecture, including its interpreter, terminal UI,
catalog and dependencies. Python is required only to develop/build from source.
Build on each target OS; verify extracted artifacts with Python absent from PATH.
Do not bootstrap the application using a system Python installation.

## Current migration

- [x] Replace the GUI with a tested Textual TUI and standalone Windows preview.
  Remove Qt widgets, dependency files and GUI tests; update entry points, native
  bundle specification and CI. Preserve the dark default. Verify navigation,
  source-aware search, combined planning, consent/elevation, background work,
  streamed logs, cancellation, durable history/recovery, manifests, runtimes,
  project changes, settings, diagnostics and manual application update checks.
- [ ] Qualify the standalone TUI on clean Windows, Linux and macOS machines.
  Verify executable permissions, Unicode, terminal resize, keyboard-only use,
  shutdown during work, missing package managers and migration of saved state.
  Building a bundle and running it on a development machine is preview evidence.
- [ ] Complete native package workflow qualification (previous milestone 16).
  Existing disposable Ubuntu/APT evidence covers installation, real upgrade,
  removal, verification, stale plans, provenance, signed-cache integrity,
  controlled cancellation and explicit post-repair recovery. Native elevation,
  broader configure/script/graceful interruption, macOS/Homebrew/casks and
  repository/key policy remain open. Keep unsupported scopes visibly explained.

## Working terminal workflows

Each workflow needs real service integration and deterministic terminal tests;
creating a menu item alone does not satisfy these acceptance criteria.

- [x] Dashboard: platform, architecture, manager availability, inventory errors.
- [x] Explore: curated/live provenance, debounce, stale-search cancellation,
  categories, platform/source selection and complete package details.
- [x] Queue: multiple requests, dependency-aware combined preview, exact commands,
  explicit consent and separate elevation, worker progress, bounded logs and
  cancellation. Inspect/acknowledge interrupted work before a fresh retry.
- [x] Installed/Updates: native version/source inventory, verification and reviewed
  removal/upgrade, with unavailable or unqualified actions explained.
- [x] Presets: starter bundles, editable user collections, safe import/export,
  unsupported cross-platform items and separate runtime/project-file review.
- [x] Runtimes: exact versions through existing mise/uv/rustup; reviewed project
  version/environment files. No silent PATH or shell edits or manager bootstrap.
- [x] Settings/History/Support: persistent preferences and output-free audit,
  guarded history clearing, diagnostics preview/export and signed-channel update
  checks. No automatic application update installation.

## Subsequent development

1. Implement and qualify missing native inventory/planning workflows, prioritizing
   Windows WinGet/Scoop so the Windows TUI can manage tools end to end.
2. Complete combined queue recovery/retry and full package metadata presentation.
3. Complete portable environment composition, project configuration and drift checks.
4. Expand discovery/providers and curated catalog only with verified mappings.
5. Broaden tested distro adapters and runtime-manager capabilities.
6. Harden settings migrations, diagnostics, cancellation and concurrent-instance safety.
7. Build native Linux/macOS standalone artifacts; add signing/notarization and
   clean-machine install/upgrade evidence before declaring a production release.
8. Publish user/contributor/catalog documentation and a reviewed release process.

Automatic release publication remains deferred. A successful TUI test or native
smoke report does not enable package mutations or prove every backend complete.
See [terminal usage](docs/tui.md), [support matrix](docs/support-matrix.md) and
[native evidence](docs/native-qualification.md) for the implemented scope.

## Migration validation (2026-11-18)

The full suite passed 560 tests with one platform-specific skip, including 18
terminal interaction tests. Ruff, formatting, mypy and catalog validation passed.
The Windows executable and extracted archive passed version, catalog and headless
TUI checks with PATH empty and Python environment overrides removed. A real
Windows terminal session opened the bundled TUI and exited with Ctrl+Q.
The archive contains its Python runtime and no Qt binaries. This is unsigned
Windows preview evidence; clean-machine and Linux/macOS validation remain open.
See [the detailed record](docs/tui-qualification.md).

The user requested seven commits for this migration: roadmap; persistent session;
terminal views/reviews; entry/build migration and GUI removal; terminal tests;
standalone runtime verification; and documentation/validation.
