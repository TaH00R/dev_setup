"""Pure curated browsing/search; availability and execution belong to adapters."""

from dataclasses import dataclass

from models.catalog import Package
from models.platform import Platform
from models.specs import InstallSpec

from .loader import validate_packages
from .schema import CATEGORIES


@dataclass(frozen=True)
class CatalogMatch:
    package: Package
    matching_specs: tuple[InstallSpec, ...]
    caveat: str


@dataclass(frozen=True)
class CatalogService:
    packages: tuple[Package, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.packages, tuple) or any(
            not isinstance(item, Package) for item in self.packages
        ):
            raise ValueError("catalog service requires an immutable tuple of packages")
        validate_packages(self.packages)

    @property
    def categories(self) -> tuple[str, ...]:
        return tuple(sorted({package.category for package in self.packages}))

    def get(self, package_id: str) -> Package | None:
        return next(
            (package for package in self.packages if package.id == package_id), None
        )

    def browse(
        self,
        *,
        category: str | None = None,
        target: Platform | None = None,
        mapped_only: bool = False,
    ) -> tuple[CatalogMatch, ...]:
        return self.search(
            "", category=category, target=target, mapped_only=mapped_only
        )

    def search(
        self,
        query: str,
        *,
        category: str | None = None,
        target: Platform | None = None,
        mapped_only: bool = False,
    ) -> tuple[CatalogMatch, ...]:
        if not isinstance(query, str):
            raise ValueError("search query must be text")
        if category is not None and category not in CATEGORIES:
            raise ValueError("unknown catalog category")
        if target is not None and not isinstance(target, Platform):
            raise ValueError("search target must be a Platform")
        if type(mapped_only) is not bool:
            raise ValueError("mapped_only must be a boolean")
        normalized = " ".join(query.casefold().split())
        terms = normalized.split()
        ranked: list[tuple[int, str, str, CatalogMatch]] = []
        for package in self.packages:
            if category is not None and package.category != category:
                continue
            native_ids = tuple(
                spec.native_id.casefold() for spec in package.install_specs
            )
            fields = (
                package.id.casefold(),
                package.name.casefold(),
                package.summary.casefold(),
                package.category,
                *package.tags,
                *native_ids,
            )
            if not all(any(term in field for field in fields) for term in terms):
                continue
            score = 5
            if normalized:
                if normalized == fields[0]:
                    score = 0
                elif normalized == fields[1]:
                    score = 1
                elif normalized in native_ids:
                    score = 2
                elif fields[1].startswith(normalized):
                    score = 3
                elif normalized in package.tags:
                    score = 4
            mappings = tuple(
                spec
                for spec in package.install_specs
                if target is not None and spec.support.supports(target)
            )
            if mapped_only and not mappings:
                continue
            if target is None:
                caveat = "Choose a target platform to review compatible mappings."
            elif not mappings:
                caveat = (
                    target.unsupported_reason
                    or "No reviewed mapping for this OS, architecture, distribution, or release."
                )
            else:
                caveat = "Reviewed mapping only; backend availability, OS requirements, version compatibility, privileges, and consent still require planning."
            ranked.append(
                (
                    score,
                    package.name.casefold(),
                    package.id,
                    CatalogMatch(package, mappings, caveat),
                )
            )
        return tuple(item[3] for item in sorted(ranked, key=lambda item: item[:3]))
