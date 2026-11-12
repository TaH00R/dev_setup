# Reproduce a development environment

The Presets page starts with ten role-based collections. These are small sets of
reviewed catalog tools, not promises of complete language stacks. The mobile
collection, for example, includes Android platform tools but does not install
Flutter, the full Android SDK or Xcode. Each collection describes its limits.

You can edit a collection and export it as JSON. The export contains stable
catalog package IDs, optional native versions/channels, runtime manager choices
and project configuration choices. It contains no commands, credentials or
absolute machine paths. Export creates a new file and refuses to overwrite an
existing one. Import accepts schema version 1 only, rejects unknown/duplicate
fields, limits each collection to 200 entries and limits the file to 256 KiB.

Importing does not install anything. Preview resolves the package IDs through
the target platform's catalog and normal planner. Missing mappings, unavailable
backends, unsupported version selection and other planner issues block the
whole review. A different tool is never silently substituted. To install a
deliberate subset, edit the collection and preview that explicit selection.
Runtime and configuration entries need their own reviews and block a combined
package-only execution until those entries are handled explicitly.

## Runtime versions

Existing native executables are required; Dev Setup does not bootstrap runtime
managers or run downloaded shell scripts. Supported execution adapters are:

| Manager | Reviewed runtimes | Implemented operations |
| --- | --- | --- |
| mise | Python, Node.js, Go | availability, manager version command, installed inventory, exact version installation |
| uv | CPython managed downloads | availability, manager version command, installed inventory, exact version installation |
| rustup | Rust host toolchains | availability, manager version command, installed inventory, exact version installation |

SDKMAN!, Conda and Mamba are staged. Runtime search, uninstall and upgrade are
not advertised as implemented. Select a full numeric version such as `3.13.2`,
`22.14.0` or `1.85.0`; moving aliases, paths, plugin identifiers and options are
refused. Reviewed platforms are Windows/macOS/Linux on x86-64 or ARM64.

A runtime preview reads native inventory and shows the exact argument array,
manager source, selected version and changes. Execution needs consent bound to
that immutable plan. It checks inventory and the native command again, passes
approval to the shared `CommandRunner`, then confirms that native inventory
reports the requested version. A successful process exit alone is insufficient.
Already-installed versions are verified and skipped. Inventory changes require
a fresh review. Cancellation, timeout, missing manager, malformed inventory and
verification failures remain distinct structured results.

Commands use `shell=False` through the same bounded, cancellable runner as
package operations. mise runs with configuration, environment loading and hooks
disabled. uv disables configuration files, installs without binary links, and
uses offline installed-inventory inspection. Custom uv TLS/mirror overrides and
rustup distribution origins are refused. Rust installation requests the exact
host triple, the minimal profile and no manager self-update. Native managers
still own their distribution downloads and installed storage; this application
does not independently verify every downloaded runtime binary. Existing mise
manager configuration outside those disabled loading features remains part of
the native manager's trust boundary.

Rustup can select its first installed toolchain as its native default when no
default exists. The plan discloses this before consent. Dev Setup does not edit
PATH or shell startup files. Cancellation can leave runtime files behind;
rollback is not implemented. Native-manager smoke tests on disposable machines
remain necessary before claiming tested support for a particular manager release.

## Project configuration

Project changes require a selected existing directory, a file/content preview
and separate approval. The supported files are:

- `.python-version`: one exact Python version.
- `rust-toolchain.toml`: a minimal managed toolchain/profile selection.
- `mise.toml`: managed Python and/or Node.js version selections, merged together.
- `.dev-setup.env`: optional declarative relative project paths for `JAVA_HOME`,
  `PYTHONPATH`, `GOPATH` and `CARGO_HOME`.

The environment file is not automatically loaded into a process or shell. It
does not accept general environment values, secrets or PATH changes. Exported
choices remain relative to the project; choosing a directory does not create
that runtime or SDK.

Existing unmanaged Rust, mise and environment configuration is refused rather
than overwritten. Existing simple Python version files can be changed after
review. File digests are checked again immediately before replacement. Linked
files/directories, Windows reparse points, hard-linked targets and paths outside
the project are refused. A changed file needs a fresh preview. The write uses a
temporary file in the same directory and atomic replacement; the pre-write
checks are not a cross-process filesystem lock or an adversarial race guarantee.
Configuration previews contain file contents for review and are kept in memory,
not written into audit logs.

## Core API

The core imports without PySide6. Run blocking preview/execution calls in a
worker thread in a desktop client:

```python
from pathlib import Path
from models.environment import RuntimeConsent, RuntimeRequest
from runtimes.service import RuntimeService
from services.environments import ProjectConfigService, starter_presets

presets = starter_presets()
runtime_service = RuntimeService(host, runner)
plan = runtime_service.preview(RuntimeRequest("uv", "python", "3.13.2"))
# Show plan.command.preview() and plan.notes; obtain a real affirmative decision.
result = runtime_service.execute(plan, RuntimeConsent(plan, confirmed=True))

config_service = ProjectConfigService()
config_plan = config_service.preview(Path.cwd(), "python", "3.13.2")
# Show config_plan.path, previous_content, content and notes before approval.
config_service.apply(config_plan, confirmed=True)
```

Reuse the same configuration service between preview and apply. It remembers
issued plans and refuses modified, invented or consumed previews. Expected
refusals use `EnvironmentError`, an `AdapterError` with a safe `.error.message`.
Do not display arbitrary exception text or persist preview contents.

## Validation and native references

Deterministic tests use mocked commands and temporary directories. They never
install tools:

```text
python -W error::ResourceWarning -m unittest tests.unit.test_runtimes tests.unit.test_environments tests.unit.test_manifests -q
```

Command construction follows [mise install](https://mise.jdx.dev/cli/install.html),
[mise inventory](https://mise.jdx.dev/cli/ls.html),
[mise global options](https://mise.jdx.dev/cli/),
[uv's CLI reference](https://docs.astral.sh/uv/reference/cli/), and
[rustup toolchains](https://rust-lang.github.io/rustup/concepts/toolchains.html).
The native rustup first-install default behavior and `--no-self-update` option
are defined in its [CLI implementation](https://github.com/rust-lang/rustup/blob/master/src/cli/rustup_mode.rs).

mise inventory always includes the explicit positional runtime argument. Its
[JSON implementation](https://github.com/jdx/mise/blob/main/src/cli/ls.rs) emits
an array of version records for this form; the tool-keyed object is the separate
all-tools form, which this adapter does not request. Runtime failures retain the
installation command result even when the subsequent verification fails. An
unexpected executor error becomes a safe structured `unknown` failure without
displaying exception text.

Approved runtime executions also return an output-free `RuntimeAudit` with the
unique operation ID, reviewed plan ID, manager, runtime, exact version, terminal
state, approval/completion timestamps and native installation exit code. The
desktop maps this metadata into persistent history. Missing, declined or
mismatched consent creates no approved-operation audit; approved skips and
failures remain auditable. Command stdout, stderr and project file contents are
never part of this record.
