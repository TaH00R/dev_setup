# Dev Setup

One place to find and manage the tools you use for development.

Dev Setup is a Python application with a PySide6 desktop interface for Windows,
macOS, and Linux. The first desktop workflow is available: browse the catalog,
inspect sources, review plans, and follow operations through their verified result.
Development is ongoing; this is not a production release.

You can use the desktop, read-only catalog commands, or the Python service API.
Installation workflows are enabled for APT on Ubuntu 24.04 and Homebrew on
macOS. The remaining adapters have tested command builders, but still need
inventory and transaction parsers before the application can use them end to end.
See the [support matrix](docs/support-matrix.md) for the details.

## Try it

You'll need Python 3.10–3.14 and a copy of this repository. Install the optional
Qt Widgets dependency to open the desktop:

On Windows (PowerShell):

```powershell
git clone https://github.com/TaH00R/dev_setup.git
cd dev_setup
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-gui.txt
.\.venv\Scripts\python.exe -m app gui
```

On macOS or Linux:

```sh
git clone https://github.com/TaH00R/dev_setup.git
cd dev_setup
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-gui.txt
.venv/bin/python -m app gui
```

Prefer the terminal? These commands need no third-party dependencies:

```sh
python -m app catalog validate
python -m app catalog search git
```

Use the Python executable from your virtual environment. Validation checks all
14 package definitions and their 39 reviewed platform
mappings. Search shows each package's native identifier, backend, and source.
These commands read local metadata and do not run package managers. A catalog
mapping alone does not mean a package can be installed on your machine.

Use `catalog search --category editors` to browse a category, or `--help` to see
all commands. `python main.py` is also available as a launcher for the same app.

## How the repository fits together

```text
app/                 Desktop bootstrap and application entry points
  ui/                PySide6 screens, review dialog, theme, and workers
  legacy/            Preserved installer menu and its older helpers
models/              Package, inventory, plan, and operation records
services/            Inventory, planning, consent, and execution
  catalog/           Catalog loading, validation, and curated search
package_managers/    Native adapters and their inspection code
system/              Platform detection and the safe command runner
catalog/             Reviewed JSON package definitions, grouped by category
tests/
  unit/              Core and adapter tests; older regressions in legacy/
  catalog/           Schema, loader, search, and shipped metadata tests
  integration/       Isolated import, launcher, runner, and fake-bridge checks
  fixtures/          Shared synthetic test data
resources/           Project assets
docs/                Architecture, API guides, and platform support
```

The core stays independent of Qt. Adapters own native commands; catalog files
contain metadata rather than scripts. Plans expose the selected source, command,
privileges, and expected effects before approval. Operations report failures and
verify native inventory before declaring success.

The desktop includes Dashboard, Explore, Installed, Updates, Queue, History, and
Settings. Search combines curated tools with live results from detected managers.
Live results retain source/native IDs and are browse-only until a reviewed mapping
is available. Searches are debounced, cached, cancellable, and can be disabled in
Settings; native searches may access the network. See the [discovery guide](docs/discovery.md).
Install, remove, and update actions for curated tools open a
plan review; administrator prompts need a separate approval. Settings, history,
and logs last for the current session. Windows can browse the catalog, but native
package management there is not enabled yet. See the [desktop guide](docs/desktop.md)
for the workflow and current limits.

## The older installer menu

The original menu is preserved in `app/legacy/` while the desktop workflow is
built. To open it, install its dependencies and choose it explicitly:

```sh
python -m pip install -r requirements.txt
python -m app legacy
```

Use the Python executable from your virtual environment for these commands. The
legacy menu uses the original installer paths, including Linux repository refresh
and system upgrade behavior. It does not use the new planning and consent service.
The new app does not load those installers during normal startup or catalog use.

## Working on Dev Setup

From an activated virtual environment:

```sh
python -m pip install -r requirements-dev.txt
python -W error::ResourceWarning -m unittest discover -s tests -t . -v
python -m app catalog validate
python -m ruff check .
python -m ruff format --check .
python -m mypy
```

Tests mock package operations. Process tests use disposable Python programs and a
fake Scoop script; they never install developer tools. Native installation,
elevation, packaging, and clean-machine behavior still need disposable-system
coverage. CI is configured for Python 3.10–3.14 on Windows, macOS, and Linux.

The next milestones are runtime managers, portable presets, production hardening,
and release packaging. More native inventory readers are also
needed to enable the remaining backends. The existing Windows build workflow
still produces a CLI preview; desktop packaging belongs to the release milestone.

Read the [development guide](docs/development.md), [architecture](docs/architecture.md),
and [operation service guide](docs/operations.md) before changing the core. For
new package definitions, start with the [catalog guide](docs/catalog.md).
Contribution details are in [CONTRIBUTING.md](CONTRIBUTING.md).
