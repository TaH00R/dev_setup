# Hybrid discovery

Explore combines the local curated catalog with searches from compatible,
detected package managers. Curated results appear immediately and rank first.
Live results retain backend, native source identity, exact native package ID,
reported version when supplied, and the platform on which the search ran.
They are visibly marked **Live (not curated)** with unknown review trust.
Discovery does not establish installation compatibility or approve a source.
Live results are browse-only; installations still require a reviewed catalog
mapping and the existing plan/consent workflow.

## Providers and source limits

`package_managers/discovery.py` owns native command selection and parsing:

| Backend | Search scope / format | Limitations |
| --- | --- | --- |
| WinGet | Explicit `winget` source; bounded English fixed-column table | Unknown/localized layouts and wide/combining-character rows fail closed rather than guessing IDs. Source alias is not verified URL provenance. |
| Scoop | Existing native entry point through the fixed `-File` bridge; typed `search-json` projection | Only search is invoked. Bucket aliases/URLs/configuration remain unverified; a reported remote bucket may not be installed. Native nonzero exits, including ambiguous no-match failures, remain provider errors. |
| Homebrew | Separate formula and cask requests, searched concurrently | Core/cask token namespaces are retained; qualified third-party taps remain distinct. Native aliases are unverified. |
| APT | `apt-cache search --names-only`; configured caches | Origin remains `apt-configured`, not an invented Ubuntu noble mapping. No repository refresh. |
| pacman | Existing sync search; repository/name and description | Repository aliases and version/architecture compatibility are unverified. No sync/update command. |
| DNF | Cache-only DNF4 colon rows / DNF5 tab rows | Native architecture-qualified IDs remain intact; configured repository origin remains unknown. |
| zypper | No-refresh XML search with details | Repository identity and native version retained; DTD/entity declarations and ambiguous XML are refused. |
| apk | No-network cached search | Native `name-version-rN` records are split without inventing a reviewed repository origin. |
| Flatpak | Per-user search; full tab-separated columns | Each remote remains a distinct result. Remote aliases, URLs, branch, permissions and installability are unverified. |

All commands use the existing argument-array runner, never shell strings. Query
validation belongs to adapters. Missing managers produce an unavailable state;
search never installs a manager, adds a bucket/repository, accepts source
agreements, elevates privileges, or performs package changes. Native search may
use the network and refresh its own metadata caches. Live discovery can be
disabled in Explore or Settings. The Scoop addition extends the already
authorized fixed bridge with a read-only action; it adds no general script API.

Output is bounded to the runner's capture limit. Truncated, malformed, or
ambiguous identities are discarded with a provider error. Terminal decoration,
control characters and bidi formatting controls are removed from display text.
Descriptions remain plain text and never become executable specifications.
No new dependency was introduced.

## Aggregation, cache, and cancellation

`services/discovery.py` is independent of Qt. Its provider protocol accepts a
query, cancellation event, and native command timeout. Up to four providers run
in parallel. Each search has a default five-second budget; cancellation cleanup
waits for owned native workers to stop. Providers must honor the event/timeout.
Native process creation or detached process limitations can delay cleanup; this
is not a promise of instantaneous cancellation.

Deduplication uses backend + source + native ID, never display name. A live hit
matching an explicit curated mapping is represented by its reviewed catalog
entry. Unknown cache origins remain distinct from reviewed repository mappings.
The final live list is bounded to 200 records, with visible truncation notices.
One failed provider does not discard curated results or another provider's hits.

Successful batches, including empty results, are cached in memory for 60 seconds
in a 64-entry LRU cache. Failures are not cached. Cache keys preserve native
query case; repeated whitespace is normalized. Cache age is visible. Refresh
live results bypasses the cache. Settings and cache are not persisted yet.

`app/ui/search.py` owns a separate QThread from package operations. Typing updates
curated matches immediately, cancels the old generation, and debounces native
dispatch. One active search and one pending latest request prevent a buildup of
obsolete searches. Generation checks reject stale snapshots, and notifications
are coalesced. Closing the window cancels pending dispatch and waits for both
search and operation workers. Partial results preserve selection and the user's
curated source/action/version choices.

Queries shorter than two characters do not search live sources. Categories apply
to reviewed catalog metadata; selecting a category pauses live providers rather
than inventing categories for remote packages. The curated platform filter does
not turn live hits into verified compatible mappings. Provider loading, cached,
unavailable, failed, timeout and cancelled states remain visible.

## Validation

Unit and GUI tests use synthetic provider output, mocked native runners and
cancellable events. Scoop bridge checks invoke a fake local entry point only.
No live package registry or native package mutation is used by ordinary tests.
Tests cover ranking, source-aware deduplication, parallel providers, stale
suppression, debounce, cache expiry/eviction, cancellation, timeout, missing
manager, no network, malformed output, provenance and partial failure.

Native references:
[WinGet search](https://learn.microsoft.com/en-us/windows/package-manager/winget/search),
[Scoop search implementation](https://github.com/ScoopInstaller/Scoop/blob/master/libexec/scoop-search.ps1),
[Homebrew search](https://docs.brew.sh/Manpage),
[DNF5 search output](https://github.com/rpm-software-management/dnf5/blob/main/libdnf5-cli/output/search.cpp),
[zypper](https://manpages.opensuse.org/Tumbleweed/zypper/zypper.8.en.html),
and [Flatpak columns](https://docs.flatpak.org/en/latest/flatpak-command-reference.html).

Local validation on Windows/CPython 3.14.7, 2026-11-12:

| Exact command | Result |
| --- | --- |
| `.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -s tests -t . -q` | 309 passed, including 28 GUI tests and fake Scoop search-JSON bridge checks. |
| `.\.venv\Scripts\python.exe -m app catalog validate` | 14 packages, 39 mappings valid. |
| `.\.venv\Scripts\python.exe -m ruff check .` | Passed. |
| `.\.venv\Scripts\python.exe -m ruff format --check .` | 88 files formatted. |
| `.\.venv\Scripts\python.exe -m mypy` | No issues in 47 source files. |

The combined curated/live screen was rendered and inspected locally with fake
providers. No live native registry query, package installation, elevation, or
remote CI execution was performed. Live-result installation and remaining native
inventory readers are still follow-up work.
