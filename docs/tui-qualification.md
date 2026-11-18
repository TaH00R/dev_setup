# Terminal migration and standalone preview validation

The 2026-11-18 pivot removes `app/ui/`, GUI tests and Qt requirements. The new
Textual front end reuses the existing core, catalog, native qualification policy,
SQLite schema, lifecycle recovery and environment services. The default is dark.
The current product plan is [steps.md](../steps.md).

## Tested terminal workflows

Headless Textual tests exercise keyboard navigation at 130x55 and resize to 80x24,
curated/category search, cancelled stale live searches with source identity,
preserved source/version selection during partial results, actual queue controls,
combined package plans, declined consent, separate elevation approval, background
execution, cancellation, waiting on quit, native inventory/update/verification,
SQLite history/recovery, explicit acknowledgement, manifest import/export,
project-file consent, exact runtime installation with post-install verification,
settings/theme persistence, reviewed diagnostic exports and unavailable storage.
Native commands/network responses are injected fakes in these tests. Package
support is still governed by the [existing qualification gates](support-matrix.md).

Executed from the repository's development virtual environment on Windows:

```powershell
.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -s tests -t . -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe -m app catalog validate
```

The suite passed **560 tests with one skip**, including **18 terminal tests**.
Ruff checked 162 files; mypy checked 91 sources. Catalog validation passed for
14 packages and 39 reviewed mappings. Replaced GUI tests are no longer counted.

## Initial standalone Windows artifact (historical snapshot)

Build command:

```powershell
.\.venv\Scripts\python.exe scripts/build_tui.py --smoke-test --archive
```

The [portable evidence record](evidence/tui-windows-x86_64-2026-11-18.json) binds the
Windows x86_64 archive by SHA-256 and size. The unsigned
`DevSetup-0.1.0.dev0-windows-x86_64.zip` is **21,284,756 bytes**, with SHA-256
`694f4690be240475dce1f7c8562d5b3b9d64dc72829e6ed48afa05194771ad56`.
Its ZIP CRC check passed; its bundled Python DLL is present and Qt binaries are
absent. PyInstaller 6.22.3 bundled the interpreter and Textual 8.2.8 with the
catalog, presets, native bridge and terminal assets.

The builder runs these absolute executable commands from an unrelated temporary
directory with `PATH` empty and `PYTHONHOME`, `PYTHONPATH`, `VIRTUAL_ENV` removed:

```text
DevSetup.exe --version
DevSetup.exe catalog validate
DevSetup.exe --self-check
DevSetup.exe --smoke-test
```

All four returned zero. The archive was also extracted into an isolated directory,
and all four checks passed against that extracted executable. A real Windows
terminal session opened the bundled dark TUI with PATH empty and app state
redirected to a build-only location; Ctrl+F opened Explore and Ctrl+Q exited zero.
No user's real state path or developer tools were modified by these checks.
End users do not need to install Python or pip.

This is a developer-machine Windows preview, not a clean-machine test, signed
release, Linux/macOS native build or package-manager qualification. Native builds
on all targets remain configured in CI, with Qt system dependencies removed.
Release publication remains deferred.

## Extracted-artifact follow-up

The build workflow now downloads the native bundle into a separate hosted-runner
job with no application dependencies installed. Before extraction it checks the
sidecar filename, size, SHA-256, operating system and architecture. Native macOS
extraction preserves framework links; Linux extraction preserves executable
permissions and applies the tar data filter. The job verifies executable access
and runs the extracted binary from another temporary directory with PATH empty.
Python environment overrides are removed and HOME, USERPROFILE, APPDATA,
LOCALAPPDATA and XDG paths are redirected to disposable directories. This tests
the shipped archive rather than relying on the builder's working bundle.

The expanded bundled smoke test navigates all nine screens, resizes to 80x24 and
140x45, renders international text, focuses search with Ctrl+F and exits with
Ctrl+Q. It disables startup discovery, and deterministic tests reject native
process creation or user-state writes. Actual missing-manager discovery,
interactive terminal rendering and saved-state migration remain separate checks.

Validation commands:

```powershell
.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -s tests -t . -q
.\.venv\Scripts\python.exe -m unittest tests.integration.test_packaging -q
.\.venv\Scripts\python.exe scripts/build_tui.py --smoke-test --archive
```

The full suite passed 563 tests with one skip; the packaging suite passed 16.
Ruff and mypy passed. The Windows archive was rebuilt and the workflow's embedded
extraction/qualification script was executed locally against it. The
[follow-up evidence](evidence/tui-windows-extracted-2026-11-18.json) records its
current hash and checks; the initial snapshot above retains its original hash.
The current local archive replaces the earlier preview at the same filename.

Hosted-runner results are not yet available. Each configured CI qualification job
uploads `standalone-qualification.json` with the artifact digest and pass/fail
result. These reports do not establish signing, interactive clean-machine
qualification or native package-manager support, and do not publish a release.
