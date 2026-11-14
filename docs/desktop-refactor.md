# Desktop refactor validation

Milestone 10 of the expanded roadmap completed locally on 2026-11-14.
This is the desktop/application refactor, distinct from the historical release
hardening step 10. No new dependencies or native package mutations were needed.

## Changed responsibilities

| Responsibility | Location |
| --- | --- |
| Window composition, inventory actions, busy state and shutdown | `app/ui/main_window.py` |
| Sidebar, page stack, heading and task footer | `app/ui/navigation.py` |
| Nine independently constructible page widgets | `app/ui/pages/` |
| Shared labels, read-only tables and scroll containers | `app/ui/components.py` |
| Search, source/action selection and preview dispatch | `app/ui/explore_presenter.py` |
| Package consent review, queue results and bounded output | `app/ui/operation_presenter.py` |
| Preferences, saved/session history and persistence notifications | `app/ui/session_presenter.py` |
| Preset/runtime/project coordination | `app/ui/environments.py` |
| Environment background jobs and review reservation | `app/ui/environment_worker.py` |
| Explicit runtime/project consent dialog | `app/ui/change_review.py` |
| Cancellable manifest I/O, preview and reviewed project writes | `application/environments.py` |
| Existing shared styling | `app/ui/theme.py` |

Page construction does not run native work. Coordinators connect controls to
existing background controllers and services. The window retains widget aliases
and bound handler access for current callers; `environments.py` retains its
dialog/controller imports. The persistence test now patches the message box in
the session presenter that owns it. The isolated import test walks `application/`
alongside the core packages while rejecting Qt, app/legacy, process and network
imports/side effects. Ruff and mypy include the new application package.

## Validation

On Windows with the existing development virtual environment:

- `.\.venv\Scripts\python.exe -W error::ResourceWarning -m unittest discover -s tests -t . -q`: **414 tests passed**, no skips. Baseline before changes: 405 tests passed.
- `.\.venv\Scripts\python.exe -m unittest tests.gui.test_pages tests.unit.test_application_workflows tests.integration.test_core_import -q`: **10 tests passed**.
- `.\.venv\Scripts\python.exe -m app catalog validate`: **14 packages / 39 reviewed mappings valid**.
- `.\.venv\Scripts\python.exe -m ruff check .`: passed.
- `.\.venv\Scripts\python.exe -m ruff format --check .`: passed, 138 files.
- `.\.venv\Scripts\python.exe -m mypy`: passed, 85 source files.
- `git diff --check` and `git diff --cached --check`: passed.

The new page tests cover keyboard navigation, focus order, accessible names,
long plain-text content, keyboard scrolling, review defaults/approval, resizing,
and scrolling to settings/runtime controls. A fresh offscreen Qt process renders
at 200% scaling and checks physical pixel dimensions and control reachability.
Workflow tests retain no-overwrite behavior, cancellation before file access,
exact consent and stale-file rejection. Existing integration tests continue to
cover search, review, progress, cancellation, shutdown, persistence and runtimes.

## Limits and next work

Offscreen Windows checks do not qualify native screen readers, multiple monitors,
other operating systems, elevation or native installations. Existing backend and
release limitations remain in the support matrix and release guide. The next
roadmap milestone is durable operation lifecycle and interrupted-work recovery;
this refactor adds neither recovery nor new persistence schema.
