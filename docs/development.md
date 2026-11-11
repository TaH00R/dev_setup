# Development baseline

The target Python range is CPython 3.10 through 3.14. The legacy code already
uses Python 3.10 structural pattern matching. CI checks every version in this
range on Windows, Linux, and macOS; a configured matrix is not proof those jobs
have passed. Packaging and actual software installation need separate testing.

Create a local virtual environment with `python -m venv .venv`, activate it, then
run `python -m pip install -r requirements-dev.txt`. On Windows activation is
`.venv\Scripts\Activate.ps1`; on POSIX shells use `. .venv/bin/activate`.

Run these commands from the repository root:

```sh
python -m unittest discover -s tests -v
python -m catalog validate
python -m ruff check .
python -m ruff format --check .
python -m mypy
git diff --check
```

Use `python -m ruff format .` to format the configured files. Ruff supplies
formatting, syntax checks, unused-import checks, and import ordering. Mypy checks
the root-level `models/`, `system/`, `catalog/`, `package_managers/`, and `services/` packages strictly. Both are
development-only dependencies. Their version ranges bound major changes;
this baseline does not provide a dependency lock.

Formatting/linting cover the new core, domain tests, the core import test, CLI
regression tests, catalog service/loader/schema tests, and `system_info.py`.
Runner, adapter, Scoop bridge, inventory inspection and operation service tests
are also in the formatting scope.
Expand scope when migrating a legacy module; avoid a repository-wide formatting
rewrite. All new core APIs need annotations. Keep
domain rules outside Qt, OS branches inside adapters, and metadata declarative.

Ordinary tests must mock package operations and network downloads. Never install
tools or upgrade the host as part of a unit test. Integration tests belong in
disposable CI environments. The core import test uses a separate isolated
interpreter without site packages, imports all modules in the root-level core
packages, and rejects legacy/GUI imports and processes. Keep core packages in
this repository root; do not recreate a nested `dev_setup/` directory.

Runner tests spawn only disposable Python processes. Bridge tests run a fake
Scoop script through native PowerShell where available and respect execution
policy. They never invoke installed Scoop or install software. See
[package-manager boundaries](package-managers.md) before changing commands,
approval handling, elevation, output observers, or the reviewed script bridge.

The legacy CLI still starts with `python main.py`. Its actual installation paths
are not a security-complete implementation of the new architecture. Use the
mocked regression suite to preserve behavior while replacing those paths.
