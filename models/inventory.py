"""Native installation evidence, independent of adapters and application services."""

from dataclasses import dataclass, field

from ._validation import identifier, text, unique_tuple
from .errors import OperationError
from .specs import DetectionSpec


@dataclass(frozen=True)
class InstalledPackage:
    native_id: str
    versions: tuple[str, ...]
    source_id: str | None = None
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        text(self.native_id, "native id")
        unique_tuple(self.versions, "installed versions")
        for version in self.versions:
            text(version, "installed version")
        if self.source_id is not None:
            identifier(self.source_id, "installed source")
        unique_tuple(self.aliases, "native aliases")
        for alias in self.aliases:
            text(alias, "native alias")
            if alias == self.native_id:
                raise ValueError("native aliases must differ from the canonical id")


@dataclass(frozen=True)
class Inventory:
    """Only complete, successfully parsed snapshots may represent absence."""

    backend: str
    packages: tuple[InstalledPackage, ...]

    def __post_init__(self) -> None:
        identifier(self.backend, "inventory backend")
        unique_tuple(self.packages, "inventory packages")
        if any(not isinstance(p, InstalledPackage) for p in self.packages):
            raise ValueError("inventory requires InstalledPackage records")
        unique_tuple(tuple(p.native_id for p in self.packages), "native ids")
        unique_tuple(
            tuple(name for p in self.packages for name in (p.native_id, *p.aliases)),
            "inventory identities",
        )

    def get(self, native_id: str) -> InstalledPackage | None:
        return next(
            (
                p
                for p in self.packages
                if p.native_id == native_id or native_id in p.aliases
            ),
            None,
        )


@dataclass(frozen=True)
class Detection:
    package_id: str
    executable: str
    present: bool
    output: str = field(default="", repr=False)
    error: OperationError | None = None

    def __post_init__(self) -> None:
        identifier(self.package_id, "detected package")
        DetectionSpec(self.executable)
        if type(self.present) is not bool:
            raise ValueError("executable presence must be a boolean")
        if not isinstance(self.output, str) or len(self.output) > 65536:
            raise ValueError("detection output must be bounded text")
        if self.error is not None and not isinstance(self.error, OperationError):
            raise ValueError("detection errors must be structured")
        if not self.present and self.output:
            raise ValueError("missing executables cannot report version output")


@dataclass(frozen=True)
class InventoryReport:
    snapshots: tuple[Inventory, ...]
    detections: tuple[Detection, ...]
    errors: tuple[tuple[str, OperationError], ...]


@dataclass(frozen=True)
class NativeReview:
    """Adapter-owned candidate evidence and native effects, never executable data."""

    version: str | None
    fingerprint: str
    changes: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.version is not None:
            text(self.version, "candidate version")
        text(self.fingerprint, "metadata fingerprint")
        unique_tuple(self.changes, "native changes")
        for change in self.changes:
            text(change, "native change")
