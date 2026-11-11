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
