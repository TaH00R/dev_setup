# Incremental migration map

Preserve the existing CLI and installers until replacement behavior has tests.
Do not connect an incomplete new service to the CLI or GUI merely to retire a
legacy module. These are target destinations, not directories to scaffold now.

| Existing module | Future responsibility | Replacement gate |
| --- | --- | --- |
| `main.py` | Legacy CLI; later a service-backed CLI alongside `app/main.py` | Menu/dispatch tests pass; new workflow has explicit review and consent. |
| `system_info.py` | Core platform detection and installed inventory | Tested normalization/distro selection and version detection, including unavailable tools. |
| Package-manager installers for Git, Python, Java, GCC, CMake, Node.js, Docker, VS Code | Catalog records + package-manager adapters + operation services | Verified IDs per backend, exact-command mocks, explicit capability and consent tests, install verification. |
| `gcc_installer.py`, `vscode_installer.py` direct-download variants | Prefer reviewed manager mappings; vendor adapter only where needed | Trusted origins, signature/checksum verification, explicit PATH/configuration consent, failure tests. |
| `installers/errors.py` | Structured core errors, translated for CLI/GUI | Useful missing-manager, timeout, permission, network, non-zero-exit, and cancellation results. |
| `installers/logger.py` | Structured events and sanitized persistent history | Redaction/privacy checks and migration tests; no passwords, secrets, or sensitive environment values. |
| Existing tests | Retained regression suite plus core/catalog/adapter/GUI suites | Tests remain deterministic; installation integration runs only in disposable environments. |
| Existing Windows build workflow | Retained legacy executable build plus new distribution automation | Clean-machine packages tested on all supported platforms; signing and update paths verified. |

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
