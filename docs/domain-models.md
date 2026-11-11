# Core domain records

The new core uses standard-library frozen dataclasses and enums. Constructors
validate domain invariants and accept immutable tuples, not mutable lists. A
future catalog loader will decode declarative data into these types and reject
unknown fields. This step does not introduce that loader or catalog files.

## Platform identity

`Platform` holds `OperatingSystem`, `Architecture`, and optional
`LinuxDistribution` (exact ID, family, ordered ID_LIKE ancestry, version, name).
`detect_platform()` inspects the host only when called; explicit OS, machine,
and os-release facts permit deterministic tests and previews for other machines.
No command, network request, or third-party distro dependency is involved.
Unknown systems, architectures, and missing/unknown distro families have an
`unsupported_reason`. This is an identity diagnostic, not proof of backend or
package availability. Actual capability/availability checks come later.

`PlatformSupport` explicitly lists architectures and, for Linux, reviewed exact
distribution IDs. Matching never expands an Ubuntu mapping to Debian just
because they share ancestry. Distribution-family detection provides context;
it does not infer package IDs or install a package manager.

## Package definitions

| Record | Fields and rules |
| --- | --- |
| `Package` | Stable lowercase ID, name, summary, category, HTTPS homepage; optional tags, docs, license, vendor, install/detection specs, dependencies, conflicts, and environment guidance. Empty install mappings represent no reviewed installation path. |
| `PackageSource` | Stable source ID/name, curated or live kind, explicit official/community/vendor/unknown trust, optional credential-free HTTPS URL. Curated origin does not imply official-repository trust. |
| `PackageVersion` | Opaque native version string and optional channel. No universal semantic-version ordering or comparison is assumed. Backend capabilities must validate selection. |
| `Dependency` | Stable package ID and optional typed version. The package aggregate rejects duplicate references, self-dependencies, self-conflicts, and dependency/conflict contradictions. Cross-package references and cycles need catalog/planning services. |
| `InstallSpec` | Explicit support, backend, exact native ID, provenance, optional version, privilege requirement, byte estimate, restart flag, and notes. Generic ID syntax validation is followed by backend-specific validation in future adapters. |
| `DetectionSpec` | Bare executable name, allowlisted version flags, bounded timeout, optional platform support. No paths, interpreter scripts, arbitrary arguments, or shell hooks. |

Text fields reject control characters and outer whitespace. Human-readable
guidance and notes are display-only data, never commands. URLs identify metadata;
they are not authorization for a vendor download. A direct-download adapter will
need trusted-origin and signature/checksum verification independently.

All examples in domain tests use synthetic package/source IDs. They do not add
catalog entries or assert that any native package exists in a registry.
