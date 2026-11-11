"""Package aggregate used by future catalog loaders and discovery services."""

from dataclasses import dataclass

from ._validation import identifier, text, unique_tuple
from .package import Dependency, https_url
from .specs import DetectionSpec, InstallSpec


@dataclass(frozen=True)
class Package:
    id: str
    name: str
    summary: str
    category: str
    homepage: str
    install_specs: tuple[InstallSpec, ...] = ()
    detection: tuple[DetectionSpec, ...] = ()
    tags: tuple[str, ...] = ()
    documentation: str | None = None
    license: str | None = None
    vendor: str | None = None
    dependencies: tuple[Dependency, ...] = ()
    conflicts: tuple[str, ...] = ()
    environment_guidance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        identifier(self.id, "package id")
        identifier(self.category, "category")
        text(self.name, "package name")
        text(self.summary, "summary")
        https_url(self.homepage, "homepage")
        if self.documentation is not None:
            https_url(self.documentation, "documentation")
        for field in ("license", "vendor"):
            value = getattr(self, field)
            if value is not None:
                text(value, field)
        for field in ("tags", "conflicts", "environment_guidance"):
            values = getattr(self, field)
            unique_tuple(values, field)
            for item in values:
                if field == "environment_guidance":
                    text(item, field)
                else:
                    identifier(item, field)
        for field, expected in (
            ("install_specs", InstallSpec),
            ("detection", DetectionSpec),
            ("dependencies", Dependency),
        ):
            values = getattr(self, field)
            unique_tuple(values, field)
            if any(not isinstance(item, expected) for item in values):
                raise ValueError(f"{field} contains an invalid record")
        dependency_ids = tuple(item.package_id for item in self.dependencies)
        unique_tuple(dependency_ids, "dependency package ids")
        if self.id in dependency_ids or self.id in self.conflicts:
            raise ValueError("a package cannot depend on or conflict with itself")
        if set(dependency_ids) & set(self.conflicts):
            raise ValueError("a dependency cannot also be a conflict")
