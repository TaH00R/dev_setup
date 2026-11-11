# Migration baseline

This records the original repository before migration. The current paths and
entry points are documented in the [migration map](migration.md#current-layout).

The starting application is an interactive CLI in `main.py`. It dispatches eight
installer classes and displays environment information from `system_info.py`.
There is no GUI, core package, curated catalog, or shared operation runner yet.

## Existing components

| Area | Current implementation | Migration concern |
| --- | --- | --- |
| Entry point | `main.py`, installer dispatch table | Preserve menu and callable entry point during migration. |
| Installation | `installers/*_installer*.py` | Repeated platform branches and package commands; no common capability contract. |
| Direct downloads | GCC and VS Code legacy installers | Verification, download bounds, and explicit system-change consent need review before reuse. |
| Detection | `system_info.py` and installer-specific probes | Inventory/version tests expect functionality absent from the starting implementation. |
| Diagnostics | `installers/errors.py`, `installers/logger.py` | Human-readable errors and file logs; no structured result/history model. |
| Dependencies | `requests`, `distro` | Used by the legacy installers; new core must import without them. |
| Tests | Nine `tests/test_*.py` modules | Mocked installer tests, host-specific assumptions, stale update-consent expectations. |
| CI | `.github/workflows/build.yml` | Windows Python 3.10 executable build; no unit-test job or OS matrix. |

## Initial validation

`python -m unittest discover -s tests -v` on Windows with Python 3.14.7:
16 tests discovered, 8 errors. Seven errors involve missing runtime dependencies
or the missing `get_tool_version` API; another expects three-element inventory
records where the implementation returns pairs. A logger test leaves a file open.
Re-run after installing `requirements.txt` to separate environment issues from
repository defects. No installation commands were intentionally executed.

## Step 1 commit sequence

1. Record repository audit and ignore local planning files.
2. Restore inventory/version detection and its failure handling.
3. Repair deterministic legacy test coverage and stale consent expectations.
4. Introduce and test a dependency-free core import boundary.
5. Define Python support, formatting, and gradual type-checking configuration.
6. Add cross-platform baseline CI while retaining the executable build.
7. Document architecture boundaries and the incremental migration map.
8. Update contributor/user instructions and record final baseline validation.

Each roadmap step uses eight focused commits. Passing local checks establish a
development baseline, not production readiness or clean-machine install support.

## Step 1 completion evidence

Validated on 2026-11-11 on Windows with CPython 3.14.7 in the local `.venv`:

| Exact command | Result |
| --- | --- |
| `.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -s tests -v` | 69 tests passed, including preserved legacy cases, CLI dispatch, consent, version failure paths, and isolated core import. |
| `.\.venv\Scripts\python.exe -m ruff check .` | All checks passed. |
| `.\.venv\Scripts\python.exe -m ruff format --check .` | Four files already formatted. |
| `.\.venv\Scripts\python.exe -m mypy` | No issues in the initial core source file. |
| `git diff --check` | Passed. |

Runtime and development dependencies were installed in the ignored virtual
environment. The inventory mismatch and missing version API were repaired;
missing-manager hints now identify WinGet, and Git/Java/GCC honor declined Linux
update consent. Tests select the intended OS explicitly rather than inheriting
the host. Logger resources are closed before handlers are replaced.

The core is an import boundary only; domain models begin in step 2. No GUI or new
package installation service is implemented. The cross-platform CI matrix has
been added but has not run remotely during this task. No real tool installation,
package upgrade, executable build, or clean-machine verification was performed.
