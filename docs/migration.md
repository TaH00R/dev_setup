# Incremental migration map

Preserve the existing CLI and installers until replacement behavior has tests.
Do not connect an incomplete new service to the CLI or GUI merely to retire a
legacy module. These are target responsibilities, not directories to scaffold now.

## Current layout

The repository cleanup moves code without removing legacy behavior:

| Previous path | Current path |
| --- | --- |
| Interactive menu in `main.py` | `app/legacy/cli.py`; start it with `python -m app legacy` |
| Root `system_info.py` and `installers/` | `app/legacy/system_info.py` and `app/legacy/installers/` |
| Python modules mixed into `catalog/` | `services/catalog/`; `catalog/` now holds JSON data |
| Flat `tests/test_*.py` | `tests/unit/`, `tests/catalog/`, `tests/integration/`, with shared `tests/fixtures/` |
| Root `image.png` | `resources/legacy-menu.png` |

`main.py` is now a small launcher for `app/main.py`, equivalent to `python -m app`.
Default startup shows help; `catalog validate` and `catalog search` are read-only.
The root-level core packages remain in place, with no nested `dev_setup/` package.
The GUI milestone has not been marked complete by this reorganization.

Older validation tables in the docs record commands and paths as they existed at
the time. Use the current commands in [development](development.md) for this layout.

## Replacement gates

| Existing module | Future responsibility | Replacement gate |
| --- | --- | --- |
| `app/legacy/cli.py` | Preserved menu alongside the new application entry point | Menu/dispatch tests pass; new installation workflow has explicit review and consent. |
| `app/legacy/system_info.py` | Core platform detection and installed inventory | Tested normalization/distro selection and version detection, including unavailable tools. |
| Package-manager installers for Git, Python, Java, GCC, CMake, Node.js, Docker, VS Code | Catalog records + package-manager adapters + operation services | Verified IDs per backend, exact-command mocks, explicit capability and consent tests, install verification. |
| `gcc_installer.py`, `vscode_installer.py` direct-download variants | Prefer reviewed manager mappings; vendor adapter only where needed | Trusted origins, signature/checksum verification, explicit PATH/configuration consent, failure tests. |
| `app/legacy/installers/errors.py` | Structured core errors, translated for CLI/GUI | Useful missing-manager, timeout, permission, network, non-zero-exit, and cancellation results. |
| `app/legacy/installers/logger.py` | Structured events and sanitized persistent history | Redaction/privacy checks and migration tests; no passwords, secrets, or sensitive environment values. |
| Existing tests | Retained regression suite plus core/catalog/adapter/GUI suites | Tests remain deterministic; installation integration runs only in disposable environments. |
| Windows build workflow | CLI preview with catalog/bridge data; later desktop distribution | Clean-machine packages tested on all supported platforms; signing and update paths verified. |

## Sequence and gates

1. Establish baseline tests, the core import boundary, architecture, and checks.
2. Implement typed domain models and platform normalization.
3. Validate the curated catalog and verify source mappings.
4. Implement the runner and first manager adapters with mocked command tests.
5. Expand reviewed backend and catalog coverage with a truthful support matrix.
6. Add inventory, dependency/conflict resolution, consent-bound plans, execution,
   verification, then capability-gated uninstall and upgrade services.
7. Add the PySide6 workflow and background workers with GUI regression tests.
8. Add hybrid live discovery, preserving identity, provenance, and partial results.
9. Add runtime managers, presets, and portable manifest previews.
10. Harden persistence, diagnostics, security, app updates, and distribution.

Run relevant tests on every behavior change, and apply security constraints
throughout; the final gate does not defer safety until release. Add catalog
categories and modules when needed, rather than creating empty scaffolding.
New dependencies need an explanation of their purpose and layer.

## Baseline limitations and follow-up

The core initially has no package operations. A passing unit suite does not
validate actual package availability or installation on Windows, macOS, or
Linux. Legacy Linux commands can refresh repositories or upgrade the host;
Git, Java, and GCC now honor their existing consent test expectations, but
this is not a complete consent system for every legacy entry point.

The supported Python target and matrix live in `docs/development.md` and
`.github/workflows/tests.yml`. The current executable workflow remains separate;
it does not yet publish the future GUI application or prove release readiness.

## Repository cleanup validation

Local Windows/CPython 3.14.7 checks on 2026-11-11:

| Exact command | Result |
| --- | --- |
| `.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -s tests -t . -q` | 237 passed, including all 227 previous tests and 10 new application/launcher checks. |
| `.\.venv\Scripts\python.exe -m app catalog validate` | 14 packages and 39 reviewed mappings valid. |
| `.\.venv\Scripts\python.exe -m app catalog search git` | Curated Git/GitHub CLI results retain native IDs, sources and platform labels. |
| `.\.venv\Scripts\python.exe main.py --help` | New launcher shows working commands. |
| `.\.venv\Scripts\python.exe -m ruff check .` | Passed. |
| `.\.venv\Scripts\python.exe -m ruff format --check .` | 69 files formatted. |
| `.\.venv\Scripts\python.exe -m mypy` | No issues in 36 source files. |
| `git diff --check` and `git diff --cached --check` | Passed. |

Local documentation links were checked for missing targets. No native package
installation or elevation was performed. The Windows CLI preview workflow now
bundles the catalog and bridge and checks its executable, but PyInstaller is not
installed locally, so the packaged executable and remote CI jobs were not run.
The desktop UI, additional native inspectors and production release checks
remain follow-up work. No new dependency was added for this reorganization.
The existing build-only PyInstaller dependency is bounded to version 6; data
arguments follow its [command reference](https://pyinstaller.org/en/stable/usage.html)
and [runtime file layout](https://pyinstaller.org/en/stable/runtime-information.html).
