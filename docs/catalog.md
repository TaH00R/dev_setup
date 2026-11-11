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
