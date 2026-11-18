# Terminal interface

Dev Setup uses Textual and opens in a terminal with a dark default. Its native
bundles contain Python and all UI dependencies: users extract the appropriate
archive and run the executable without installing Python or pip. Source checkouts
require the development environment described in the [README](../README.md).

## Navigation and safety

Use arrows/Enter in the navigation list, Tab/Shift+Tab between controls, Ctrl+F for
Explore, F5 for refresh, Ctrl+C to cancel work and Ctrl+Q to quit. Pages scroll on
small terminals and support keyboard-only interaction. Exit waits for owned tasks;
cancellation can leave partial native changes and does not promise rollback.

Native operations, searches and state I/O run in background workers. Stale searches
are cancelled; late results cannot replace newer queries. Native descriptions and
output are plain text with terminal control characters removed. Logs are bounded,
redacted and session-only; audit history stores operation metadata, not raw output.

## Packages and combined plans

Explore identifies curated versus live results and retains source/native IDs.
Live results are browse-only until a reviewed catalog mapping exists. Select a
curated package and its platform/backend/source, then add install, upgrade or
removal requests to the Queue. Review the combined dependency-aware plan before
execution. Verify requests are available separately.

The review includes exact commands, candidate versions, provenance, dependencies,
privileges, effects and unsupported reasons. Consent defaults to decline; native
administrator prompting requires a second checkbox. A blocked plan cannot be
approved. The TUI uses the same [native qualification gate](support-matrix.md) as
the core. A menu or successfully constructed command does not establish support.

Installed refreshes native inventory and exposes mapped tools for inspection.
Updates checks reviewed candidates and prepares combined upgrade review. Inventory
errors are displayed explicitly. The normal application currently keeps package
mutations disabled while qualification is incomplete; Windows native inventory
and management remain roadmap work.

## Queue, history and recovery

Queue shows requests, operation state and bounded stdout/stderr snapshots. Work
continues while users navigate. Cancel signals the existing core runner, which
terminates its owned process scope where supported. Verified results and failures
are recorded in History and durable lifecycle storage.

After interruption, inspect the selected recovery item. Acknowledgement requires
an inspectable outcome; unknown/partial inventory cannot be reconciled. History
can prepare a fresh retry for review after recovery. Clearing audit history
requires confirmation and preserves unresolved lifecycle evidence. Storage failure
keeps browsing available and blocks persistent mutations rather than losing their
journal. See [recovery](lifecycle.md) and [native safety](native-safety.md).

## Presets, runtimes and project files

Presets provides ten starter collections and editable names/package lists.
Import preserves exact package versions, runtimes, configuration and environment
choices; export writes a new manifest file after review. Existing files are not
silently overwritten. Cross-platform package resolution reports unsupported items.

Runtimes reviews exact versions through existing mise, uv and rustup managers.
Runtime selections may be added to a collection. Project configuration supports
Python/Rust version files and mise Python/Node settings. Project-relative
JAVA_HOME, PYTHONPATH, GOPATH and CARGO_HOME guidance uses separately reviewed
files; no global PATH, registry or shell-startup changes are made. Add these choices
to a collection or preview/apply them individually. File review shows original and
proposed content; changed inputs require a new plan. Runtime and project actions
require separate review from a collection's package plan.

## Preferences and support

Settings saves dark/light theme, live-search preference and debounce duration.
Preferences and history use the existing SQLite location and schema. Diagnostics
are previewed before export to a new file; paths, environment values and process
output are excluded. Application updates are manual and remain disabled without
a trusted signed channel; no automatic update installation is provided.

## Standalone verification

`python scripts/build_tui.py --smoke-test --archive` builds on the native target OS
and runs version/catalog/headless TUI checks with no Python on PATH, from outside
the repository. The interpreter and Textual assets are included and Qt is excluded.
Windows previews are unsigned; macOS/Linux native builds and clean-machine tests
must be verified separately before making production support claims.
