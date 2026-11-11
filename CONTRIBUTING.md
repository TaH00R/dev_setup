# Contributing

1. Fork the repository
2. Create a feature branch
3. Follow [development setup and checks](docs/development.md).
4. Make a focused change with deterministic tests, then submit a Pull Request.

## Architecture and migration

Read [architecture boundaries](docs/architecture.md) and the
[migration map](docs/migration.md) before changing installers. Preserve the
legacy CLI until equivalent behavior is tested. The root-level `models` and
`system` packages must
import without Qt, the CLI, or legacy installer dependencies.

The root-level `catalog/` package and its JSON entries follow the
[catalog contribution guide](docs/catalog.md). Run `python -m catalog validate`
alongside the unit and quality checks. Verify each mapping against its source;
record the review URL/date, tested architecture scope, platform constraints,
and native service/system changes. Ordinary tests must remain offline.

The baseline targets Python 3.10-3.14. Run the unit suite, Ruff lint/format checks,
mypy, and `git diff --check` using the commands in the development guide. Report
the exact checks run and any limitations in your pull request. CI covers the
target Python/OS matrix; real installation tests require disposable environments.

New package definitions must use verified IDs and declarative metadata. Keep
native command construction in adapters, use argument arrays with `shell=False`,
and require explicit review/consent for system changes. Explain new dependencies.
Never include secrets, machine-specific configuration, or build artifacts.

## Beginner Friendly Tasks

- Add reviewed catalog mappings as the catalog is introduced
- Improve documentation
- Add OS support
- Add package detection
