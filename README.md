# Dev Setup

A keyboard-first terminal application for discovering and managing developer tools
on Windows, Linux and macOS. The Qt GUI has been replaced by a Textual TUI.

**End users do not need Python or pip.** Native distributions bundle the
interpreter, terminal interface, catalog and runtime dependencies.

## Run the standalone app

Extract the archive for your OS and architecture, keep its bundled files together,
and open a terminal in the extracted directory:

| Platform | Launch |
| --- | --- |
| Windows | `.\DevSetup\DevSetup.exe` |
| Linux | `./DevSetup/DevSetup` |
| macOS | `./DevSetup.app/Contents/MacOS/DevSetup` |

The local Windows preview is built at `dist/DevSetup/DevSetup.exe`; distributable
archives and SHA-256 metadata are written to `dist/`. Preview builds are unsigned,
and production releases have not been published. Native builds and clean-machine
qualification are required for each platform.

Run the same executable with `--help`, `--version`, `catalog validate` or
`catalog search git` for noninteractive use. These catalog commands read local
metadata and do not install tools. The application can manage a Python toolchain
without depending on a separately installed Python runtime for its own startup.

## Terminal workflows

- Dashboard: platform, architecture, manager availability and inventory errors.
- Explore: category filters, curated/live search provenance, platform/source
  selection, package details and requests for one combined reviewed plan.
- Installed and Updates: native versions/sources and reviewed management actions.
- Queue: exact command review, separate elevation consent, background operations,
  bounded streamed output, cancellation and durable interrupted-work inspection.
- Presets: starter bundles, editable collections and portable manifest import/export.
- Runtimes: exact versions through existing mise, uv and rustup; separately
  reviewed project version files and project-local environment guidance.
- History and Settings: persistent audit/preferences, dark default, guarded history
  clearing, diagnostics preview/export and manual signed-channel update checks.

Use Tab/Shift+Tab to move focus, arrows and Enter for navigation, Ctrl+F to search,
F5 to refresh, Ctrl+C to request cancellation and Ctrl+Q to quit. Shutdown waits
for owned work to finish or stop. Terminal metadata is rendered as plain text.

Support depends on the native backend and platform. Package mutation qualification
is still pending, so production install/update/remove controls explain that gate;
they do not silently bypass it. Windows inventory/orchestration remains follow-up
work. The TUI preserves the core's consent, verification and recovery protections.
See the [support matrix](docs/support-matrix.md), [terminal guide](docs/tui.md) and
[roadmap](steps.md) for implemented workflows and remaining work.

## Develop from source

Python 3.10?3.14 is a **development requirement**, not an end-user prerequisite.

```sh
python -m venv .venv
# Activate the environment using the appropriate command for your shell.
python -m pip install -r requirements-dev.txt
python -m app tui
```

`python -m app` opens the TUI in an interactive terminal. Piped/headless invocation
prints help; catalog commands stay usable without loading the interface.
`python main.py` is an equivalent source launcher.

Build a standalone native distribution on the target OS:

```sh
python -m pip install -r requirements-tui.txt -r requirements-security.txt "pyinstaller>=6.22,<7"
python scripts/build_tui.py --smoke-test --archive
```

The smoke checks run the absolute bundled executable from an unrelated directory
with an empty PATH and Python environment overrides removed. They exercise its
version, catalog validation and headless TUI startup using the embedded runtime.
CI builds native Windows, Linux and macOS artifacts; no GUI libraries are needed.
Signing/notarization and release publication retain their separate approval gates.
See [distribution](docs/release.md).

## Repository

```text
app/tui/             Terminal pages, review screens and background-work routing
application/         UI-independent persistent session and environment workflows
services/            Catalog, discovery, inventory, planning, operations and recovery
models/              Typed package, runtime, plan and operation records
package_managers/    Native adapters and reviewed inspection code
runtimes/            Version-manager adapters
persistence/         SQLite preferences, history and durable lifecycle records
system/              Platform integration, safe process runner and diagnostics
catalog/             Reviewed JSON package definitions
packaging/           Standalone native bundle specification
scripts/             TUI builds, signing and guarded native qualification
app/legacy/          Preserved original source-only installer menu
```

The core imports without Textual, Qt or third-party libraries. Native commands use
argument arrays and `shell=False`; catalog entries contain metadata, not scripts.

## Validate changes

```sh
python -W error::ResourceWarning -m unittest discover -s tests -t . -v
python -m app catalog validate
python -m ruff check .
python -m ruff format --check .
python -m mypy
```

Terminal interaction tests use injected fake backends and temporary state. Ordinary
tests never install developer tools. Real native qualification runs only in
explicitly approved disposable environments. The original installer menu remains
source-only via `python -m app legacy` with `requirements.txt`; it does not use the
new planning/consent services and is excluded from standalone distributions.
