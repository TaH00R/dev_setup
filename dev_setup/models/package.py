"""Package identity and provenance, independent of catalogs and adapters."""

from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit

from ._validation import enum_value, identifier, text


def https_url(value: str, field: str) -> None:
    text(value, field)
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and not any(char.isspace() for char in value)
        )
        parsed.port  # Reject invalid port syntax.
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(f"{field} must be an HTTPS URL without credentials")


class SourceKind(str, Enum):
    CURATED = "curated"
    LIVE = "live"


class TrustLevel(str, Enum):
    OFFICIAL_REPOSITORY = "official_repository"
    COMMUNITY_REPOSITORY = "community_repository"
    DIRECT_VENDOR = "direct_vendor"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PackageSource:
    id: str
    name: str
    kind: SourceKind
    trust: TrustLevel
    url: str | None = None

    def __post_init__(self) -> None:
        identifier(self.id, "source id")
        text(self.name, "source name")
        enum_value(self.kind, SourceKind, "source kind")
        enum_value(self.trust, TrustLevel, "source trust")
        if self.url is not None:
            https_url(self.url, "source URL")


@dataclass(frozen=True)
class PackageVersion:
    """Opaque native version/channel; adapters decide whether selection is supported."""

    value: str
    channel: str | None = None

    def __post_init__(self) -> None:
        text(self.value, "version")
        if self.channel is not None:
            text(self.channel, "channel")


@dataclass(frozen=True)
class Dependency:
    package_id: str
    version: PackageVersion | None = None

    def __post_init__(self) -> None:
        identifier(self.package_id, "dependency package id")
        if self.version is not None and not isinstance(self.version, PackageVersion):
            raise ValueError("dependency version must be a PackageVersion")
