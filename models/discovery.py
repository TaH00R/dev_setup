"""Bounded discovery records; live metadata can never grant installation consent."""

from dataclasses import dataclass
from enum import Enum

from ._validation import identifier, text
from .errors import OperationError
from .package import SourceKind, TrustLevel
from .platform import Platform


@dataclass(frozen=True)
class LivePackage:
    backend: str
    source_id: str
    native_id: str
    name: str
    summary: str
    platform: Platform
    version: str | None = None
    caveats: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        identifier(self.backend, "discovery backend")
        for field in ("source_id", "native_id", "name"):
            value = getattr(self, field)
            text(value, field)
            if len(value) > 256:
                raise ValueError("discovery identity exceeds its display bound")
        if not isinstance(self.summary, str) or len(self.summary) > 1024:
            raise ValueError("discovery summary must be bounded text")
        if self.summary:
            text(self.summary, "discovery summary")
        if not isinstance(self.platform, Platform):
            raise ValueError("discovery requires its search platform")
        if self.version is not None:
            text(self.version, "discovered version")
            if len(self.version) > 256:
                raise ValueError("discovered version is too long")
        if not isinstance(self.caveats, tuple):
            raise ValueError("discovery caveats must be immutable")
        for caveat in self.caveats:
            text(caveat, "discovery caveat")

    @property
    def key(self) -> tuple[str, str, str]:
        return self.backend, self.source_id, self.native_id

    @property
    def kind(self) -> SourceKind:
        return SourceKind.LIVE

    @property
    def trust(self) -> TrustLevel:
        return TrustLevel.UNKNOWN

    @property
    def installable(self) -> bool:
        return False


@dataclass(frozen=True)
class ProviderBatch:
    packages: tuple[LivePackage, ...]
    limited: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.packages, tuple) or any(
            not isinstance(p, LivePackage) for p in self.packages
        ):
            raise ValueError("provider output must contain immutable live records")
        if type(self.limited) is not bool:
            raise ValueError("provider limit flag must be a boolean")


class SearchState(str, Enum):
    SEARCHING = "searching"
    SUCCEEDED = "succeeded"
    CACHED = "cached"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class ProviderStatus:
    provider_id: str
    state: SearchState
    count: int = 0
    error: OperationError | None = None
    limited: bool = False
    cache_age_seconds: float | None = None

    def __post_init__(self) -> None:
        identifier(self.provider_id, "search provider")
        if (
            not isinstance(self.state, SearchState)
            or type(self.count) is not int
            or self.count < 0
        ):
            raise ValueError("invalid provider search state/count")
        if self.error is not None and not isinstance(self.error, OperationError):
            raise ValueError("provider errors must be structured")
