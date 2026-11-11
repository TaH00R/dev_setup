# Curated catalog contract (schema version 1)

Catalog data and services live in root-level `catalog/`; no nested project
directory is used. Each `catalog/<category>/<id>.json` contains one JSON object:
`schema_version` (the integer `1`) and `package` (an object). The standard-library
decoder is the executable schema; no additional validation dependency is needed.
Unknown fields are rejected at every level, including nested source, support,
detection, dependency, version, and review records. Shell strings, executable
hooks, installer scripts, and arbitrary command arrays are not schema fields.

## Field reference

Required package fields: `id`, `name`, `summary`, `category`, `homepage`,
`install_specs` (array), and `detection` (array). Optional fields: `tags` (string
array), `documentation`, `license`, `vendor`, `dependencies` (record array),
`conflicts` (package-ID array), `environment_guidance` (display-only string array).
Identifiers, URLs, records, and reference invariants follow
[the domain contract](domain-models.md).

Every install spec requires `support`, `backend`, `native_id`, `source`,
`privilege`, and `review`. Optional: `version`, `estimated_size_bytes`,
`restart_required`, `notes`. Estimated sizes are integer byte counts or null,
never booleans. Optional text/version values may be null; array fields must
remain arrays rather than null. Backend and privilege are explicit.

| Nested record | Required fields | Optional fields |
| --- | --- | --- |
| support | os, architectures | distributions, distribution_versions |
| source | id, name, kind, trust, url | None |
| review | url, checked_on (YYYY-MM-DD) | None |
| detection | executable, version_args | timeout_seconds, support |
| dependency | package_id | version |
| version | value | channel |

`source.kind` must be `curated`; live search results use a separate discovery
flow. Review URLs and dates record manual source verification, not a successful
installation test. Metadata must not contain credentials. Backend/OS and known
Linux backend/family mismatches are rejected. Source trust remains independent
of curated origin: community-maintained manifests are not vendor repositories.

Categories: languages, runtimes, compilers, build-tools, editors, databases,
containers, cloud-devops, api-network, data-ml, mobile, utilities. Add categories
by a reviewed schema change. Adding a backend requires its reviewed schema
mapping and eventually an adapter; catalog presence does not implement one.

## File and catalog validation

`load_catalog()` loads the bundled directory by default; pass a `Path` to load
another local catalog. Loading performs no network requests or package-manager
commands. It returns an immutable, ID-sorted tuple and fails closed on any
invalid document. Missing/empty catalogs are errors rather than silent success.

Documents must be UTF-8 JSON, at most 256 KiB, with no duplicate object keys or
non-finite numbers. Symlink entries are rejected, and documents must live exactly
at `<category>/<id>.json`, matching their own metadata. Whole-catalog validation
rejects duplicate package IDs/native mappings, inconsistent shared source IDs,
missing detection rules for mapped packages, and unresolved dependency/conflict
references. Dependency resolution/cycle handling remains the planner's job.

## Browsing and search

`CatalogService(load_catalog())` provides `categories`, `get(id)`, `browse()`,
and `search(query, category=None, target=None, mapped_only=False)`. Searches are
case-insensitive, normalize whitespace, and require all query terms to match
some combination of ID, name, summary, category, tags, or native identifiers.
Exact IDs, names, and native IDs rank ahead of prefix/tag/general matches;
ties sort by name then stable ID. Results are deterministic and retain source
identity through their package and matched install specs.

Unsupported results remain visible with a reason. `mapped_only=True` filters
to reviewed mappings for an explicit target platform. No target means no
assertion of compatibility. A matching spec is not proof of installability:
backend availability, native OS/version requirements, privileges, and consent
still need future adapters/planning. These services never detect managers or
perform live package search; hybrid discovery comes later.

## Initial review scope

Language/runtime/compiler entries start with Python 3, Node.js, OpenJDK 21,
and the GNU C/C++ toolchain. Mappings record exact source evidence URLs and
review dates. WinGet x64 manifests, Homebrew ARM64 formula metadata, and
Ubuntu 24.04 amd64 package pages were checked manually; automated tests are
offline and validate the saved contracts rather than re-querying registries.
No real installation has been performed.

Native versions, included tooling, and environment integration differ by
source. Node is a runtime and npm is tooling; an Ubuntu Python mapping is
release-native rather than a promise of Python 3.14. GNU compilers on macOS
use versioned executables; the Apple `gcc` alias is not treated as GNU GCC.
The reviewed rolling Homebrew GCC detection is `gcc-16` and needs re-review
when the formula's major version changes. Package-manager inventory and
selected-version verification will be implemented with the operation services.

## Bundled packages and platform matrix

There are 10 entries and 26 reviewed mappings. Docker Desktop and Engine have
separate IDs and will not be substituted across platforms.

| Package ID | Windows x86_64 / WinGet | macOS ARM64 / Homebrew | Ubuntu 24.04 x86_64 / APT |
| --- | --- | --- | --- |
| git | Git.Git | git (formula) | git |
| python | Python.Python.3.14 | python@3.14 (formula) | python3 |
| nodejs | OpenJS.NodeJS.LTS | node (formula) | nodejs |
| java | Microsoft.OpenJDK.21 | openjdk@21 (formula) | openjdk-21-jdk |
| gcc | BrechtSanders.WinLibs.POSIX.UCRT | gcc (formula) | build-essential |
| cmake | Kitware.CMake | cmake (formula) | cmake |
| vscode | Microsoft.VisualStudioCode | visual-studio-code (cask) | No reviewed mapping |
| docker-desktop | Docker.DockerDesktop | docker-desktop (cask) | No reviewed mapping |
| docker-engine | No reviewed mapping | No reviewed mapping | docker.io |
| postgresql | PostgreSQL.PostgreSQL.17 | postgresql@17 (formula) | postgresql |

Every entry links the exact manifest/API/package page in `install_specs.review`.
WinGet and Homebrew metadata are community-maintained; Ubuntu distribution
repositories are official distribution sources. Neither label implies that a
package is vendor-authored. A complete real document is
[Git's entry](../catalog/api-network/git.json).

These are reviewed identity/architecture mappings, not a declaration that every
OS release supports installation. Intel macOS, other CPU architectures, Linux
distributions/releases beyond Ubuntu 24.04, and other backends are intentionally
unmapped until reviewed. Native OS-version requirements remain caveats for the
future planner to check. Homebrew formula versus cask provenance stays explicit
so adapters can choose the correct native operation rather than guessing.

VS Code's proprietary binary license and external Linux repository are called
out. Docker Desktop's subscription/virtualization requirements are separate from
Engine service/privilege changes. PostgreSQL native packages may initialize a
cluster or start a service. These effects must appear in a future consent flow;
the catalog neither runs initialization nor silently exposes a database.

Source review also consulted [Docker's Windows requirements and terms](https://docs.docker.com/desktop/setup/install/windows-install/),
[Ubuntu PostgreSQL setup](https://ubuntu.com/server/docs/how-to/databases/install-postgresql/),
and [Homebrew PostgreSQL service/cluster notes](https://formulae.brew.sh/formula/postgresql@17).
Versions and native side effects can change; adapters must refresh source
metadata and build an exact plan before offering execution.

## Contribution workflow

1. Inspect the source's exact package ID, distribution/release, architecture,
   licensing, dependencies, and native service/environment changes. Prefer
   package-manager manifests, official formula/cask APIs, and distribution
   package pages. Do not infer identifiers from a different OS or distro.
2. Add or update `<category>/<id>.json` using the field reference above. Keep
   architecture/release coverage narrow, record evidence URL/date for every
   mapping, and distinguish source trust from curated origin. Explain known
   gaps, version differences, native dependencies, and restart/size caveats.
3. Add detection coverage for the mapped platforms, and add offline regression
   cases for package-specific risks. Detection of a client executable alone is
   not proof of a server/product installation; service/native inventory checks
   remain necessary. Versioned executables need re-review when packages change.
4. Run `python -m catalog validate`, the full unit suite, Ruff, mypy, and
   `git diff --check`. Do not query registries or install packages in ordinary
   tests. Installation integration belongs in disposable systems.

Use `python -m catalog validate --directory PATH` to validate another local
catalog. The command reports a non-zero exit on failure, includes safe relative
file context for invalid documents, and never starts a package operation.

## Step 3 validation evidence

Validated locally on Windows/CPython 3.14.7 on 2026-11-11:

| Exact command | Result |
| --- | --- |
| `.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -s tests -v` | 138 passed, including 25 catalog tests and all legacy/core regressions. |
| `.\.venv\Scripts\python.exe -m catalog validate` | 10 packages, 26 reviewed mappings validated. |
| `.\.venv\Scripts\python.exe -m ruff check .` | All checks passed. |
| `.\.venv\Scripts\python.exe -m ruff format --check .` | 28 files already formatted. |
| `.\.venv\Scripts\python.exe -m mypy` | No issues in 16 core/catalog source files. |
| `git diff --check` | Passed. |

The isolated-import regression now imports every models/system/catalog module
without site packages and rejects legacy/GUI dependencies, processes, and network
requests. Catalog tests use local documents and synthetic fixtures, not live
registries. Source verification was a separate manual read-only review.
No package installation, native package-manager command, or clean-machine test
was performed. Remote Windows/macOS/Linux CI has not run during this local task.
Adapters, operation planning/execution, live search, and GUI integration remain
future steps. No new dependency was introduced for catalog validation.
