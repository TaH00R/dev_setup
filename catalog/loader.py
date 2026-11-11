"""Bounded local JSON loading and whole-catalog validation, without network I/O."""

import json
from pathlib import Path

from models.catalog import Package
from models.package import PackageSource

from .schema import CatalogValidationError, decode_package

MAX_DOCUMENT_BYTES = 262144


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CatalogValidationError("document: duplicate JSON keys are forbidden")
        result[key] = value
    return result


def load_package(path: Path) -> Package:
    """Read one document, rejecting duplicate keys and oversized/nonstandard JSON."""
    try:
        if path.is_symlink():
            raise CatalogValidationError("document: symbolic links are forbidden")
        with path.open("rb") as stream:
            raw = stream.read(MAX_DOCUMENT_BYTES + 1)
        if len(raw) > MAX_DOCUMENT_BYTES:
            raise CatalogValidationError("document: maximum size is 256 KiB")

        def reject_constant(value: str) -> object:
            raise CatalogValidationError(
                "document: non-finite JSON numbers are forbidden"
            )

        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=reject_constant,
        )
        return decode_package(document)
    except CatalogValidationError:
        raise
    except (OSError, UnicodeError, ValueError, RecursionError) as error:
        raise CatalogValidationError(
            "document: cannot read valid UTF-8 catalog JSON"
        ) from error


def validate_packages(packages: tuple[Package, ...]) -> None:
    """Check shared source identity, unique mappings, and cross-package references."""
    by_id: dict[str, Package] = {}
    sources: dict[str, PackageSource] = {}
    for package in packages:
        if package.id in by_id:
            raise CatalogValidationError("catalog: duplicate package IDs")
        by_id[package.id] = package
        mapping_keys = set()
        for spec in package.install_specs:
            previous = sources.setdefault(spec.source.id, spec.source)
            if previous != spec.source:
                raise CatalogValidationError("catalog: inconsistent source identity")
            key = (
                spec.backend,
                spec.native_id,
                spec.source.id,
                spec.support,
                spec.version,
            )
            if key in mapping_keys:
                raise CatalogValidationError("catalog: duplicate native mapping")
            mapping_keys.add(key)
        if package.install_specs and not package.detection:
            raise CatalogValidationError(
                "catalog: mapped packages require detection rules"
            )
    for package in packages:
        references = {dep.package_id for dep in package.dependencies} | set(
            package.conflicts
        )
        if references - by_id.keys():
            raise CatalogValidationError(
                "catalog: unresolved dependency or conflict reference"
            )


def load_catalog(root: Path | None = None) -> tuple[Package, ...]:
    """Load <category>/<id>.json in deterministic order; all errors fail closed."""
    directory = (Path(__file__).parent if root is None else root).resolve()
    try:
        if not directory.is_dir():
            raise CatalogValidationError("catalog: directory does not exist")
        if any(path.is_symlink() for path in directory.iterdir()):
            raise CatalogValidationError("catalog: symbolic links are forbidden")
        packages = []
        for path in sorted(directory.rglob("*.json")):
            relative = path.relative_to(directory)
            if len(relative.parts) != 2 or not path.resolve().is_relative_to(directory):
                raise CatalogValidationError(
                    "catalog: documents must be category/id.json"
                )
            package = load_package(path)
            if relative.parent.name != package.category or path.stem != package.id:
                raise CatalogValidationError(
                    "catalog: path must match package category and ID"
                )
            packages.append(package)
        if not packages:
            raise CatalogValidationError("catalog: no package documents found")
        snapshot = tuple(sorted(packages, key=lambda package: package.id))
        validate_packages(snapshot)
        return snapshot
    except OSError as error:
        raise CatalogValidationError("catalog: directory cannot be read") from error
