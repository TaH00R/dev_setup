"""Strict version-1 JSON decoding using the standard library and domain models."""

import re
from datetime import date
from typing import cast

from models.catalog import Package
from models.package import (
    Dependency,
    PackageSource,
    PackageVersion,
    SourceKind,
    TrustLevel,
)
from models.platform import Architecture, LinuxFamily, OperatingSystem
from models.specs import (
    DetectionSpec,
    InstallSpec,
    MappingReview,
    PlatformSupport,
    PrivilegeRequirement,
)
from system.platform import distribution_from_release

CATEGORIES = frozenset(
    {
        "languages",
        "runtimes",
        "compilers",
        "build-tools",
        "editors",
        "databases",
        "containers",
        "cloud-devops",
        "api-network",
        "data-ml",
        "mobile",
        "utilities",
    }
)

_BACKEND_OS = {
    "winget": OperatingSystem.WINDOWS,
    "scoop": OperatingSystem.WINDOWS,
    "chocolatey": OperatingSystem.WINDOWS,
    "homebrew": OperatingSystem.MACOS,
    "macports": OperatingSystem.MACOS,
    **dict.fromkeys(
        ("apt", "pacman", "dnf", "zypper", "apk", "flatpak", "snap"),
        OperatingSystem.LINUX,
    ),
}
_BACKEND_FAMILY = {
    "apt": LinuxFamily.DEBIAN,
    "pacman": LinuxFamily.ARCH,
    "dnf": LinuxFamily.FEDORA,
    "zypper": LinuxFamily.SUSE,
    "apk": LinuxFamily.ALPINE,
}


class CatalogValidationError(ValueError):
    """A document violates the catalog contract; messages contain paths, not values."""


def record(value: object, fields: str, required: str, path: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise CatalogValidationError(f"{path}: expected an object")
    result = cast(dict[str, object], value)
    if set(result) - set(fields.split()):
        raise CatalogValidationError(f"{path}: unknown fields are forbidden")
    if set(required.split()) - set(result):
        raise CatalogValidationError(f"{path}: required fields are missing")
    return result


def string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise CatalogValidationError(f"{path}: expected a string")
    return value


def optional_string(value: object, path: str) -> str | None:
    return None if value is None else string(value, path)


def array(value: object, path: str) -> list[object]:
    if not isinstance(value, list):
        raise CatalogValidationError(f"{path}: expected an array")
    return cast(list[object], value)


def strings(value: object, path: str) -> tuple[str, ...]:
    return tuple(string(item, path) for item in array(value, path))


def boolean(value: object, path: str) -> bool:
    if type(value) is not bool:
        raise CatalogValidationError(f"{path}: expected a boolean")
    return value


def support(value: object) -> PlatformSupport:
    obj = record(
        value,
        "os architectures distributions distribution_versions",
        "os architectures",
        "support",
    )
    return PlatformSupport(
        OperatingSystem(string(obj["os"], "support.os")),
        tuple(
            Architecture(item)
            for item in strings(obj["architectures"], "support.architectures")
        ),
        strings(obj.get("distributions", []), "support.distributions"),
        strings(obj.get("distribution_versions", []), "support.distribution_versions"),
    )


def version(value: object) -> PackageVersion | None:
    if value is None:
        return None
    obj = record(value, "value channel", "value", "version")
    return PackageVersion(
        string(obj["value"], "version.value"),
        optional_string(obj.get("channel"), "version.channel"),
    )


def source(value: object) -> PackageSource:
    obj = record(value, "id name kind trust url", "id name kind trust url", "source")
    result = PackageSource(
        string(obj["id"], "source.id"),
        string(obj["name"], "source.name"),
        SourceKind(string(obj["kind"], "source.kind")),
        TrustLevel(string(obj["trust"], "source.trust")),
        string(obj["url"], "source.url"),
    )
    if result.kind != SourceKind.CURATED:
        raise CatalogValidationError(
            "source.kind: curated files cannot claim live provenance"
        )
    return result


def install_spec(value: object) -> InstallSpec:
    obj = record(
        value,
        "support backend native_id source version privilege estimated_size_bytes restart_required notes review",
        "support backend native_id source privilege review",
        "install_spec",
    )
    review = record(obj["review"], "url checked_on", "url checked_on", "review")
    checked_on = string(review["checked_on"], "review.checked_on")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", checked_on) is None:
        raise CatalogValidationError("review.checked_on: expected YYYY-MM-DD")
    size = obj.get("estimated_size_bytes")
    if size is not None and type(size) is not int:
        raise CatalogValidationError(
            "estimated_size_bytes: expected an integer or null"
        )
    result = InstallSpec(
        support(obj["support"]),
        string(obj["backend"], "backend"),
        string(obj["native_id"], "native_id"),
        source(obj["source"]),
        version(obj.get("version")),
        PrivilegeRequirement(string(obj["privilege"], "privilege")),
        size,
        boolean(obj.get("restart_required", False), "restart_required"),
        strings(obj.get("notes", []), "notes"),
        MappingReview(
            string(review["url"], "review.url"), date.fromisoformat(checked_on)
        ),
    )
    if _BACKEND_OS.get(result.backend) != result.support.os:
        raise CatalogValidationError(
            "backend: unknown backend or incompatible operating system"
        )
    expected = _BACKEND_FAMILY.get(result.backend)
    for distro in result.support.distributions:
        detected = distribution_from_release({"ID": distro})
        if expected is not None and (detected is None or detected.family != expected):
            raise CatalogValidationError(
                "support.distributions: incompatible backend family"
            )
    return result


def detection(value: object) -> DetectionSpec:
    obj = record(
        value,
        "executable version_args timeout_seconds support",
        "executable version_args",
        "detection",
    )
    timeout = obj.get("timeout_seconds", 5.0)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise CatalogValidationError("detection.timeout_seconds: expected a number")
    return DetectionSpec(
        string(obj["executable"], "detection.executable"),
        strings(obj["version_args"], "detection.version_args"),
        timeout,
        support(obj["support"]) if obj.get("support") is not None else None,
    )


def dependency(value: object) -> Dependency:
    obj = record(value, "package_id version", "package_id", "dependency")
    return Dependency(
        string(obj["package_id"], "dependency.package_id"), version(obj.get("version"))
    )


def decode_package(document: object) -> Package:
    """Reject unknown keys at every level before constructing validated records."""
    try:
        root = record(
            document, "schema_version package", "schema_version package", "document"
        )
        if type(root["schema_version"]) is not int or root["schema_version"] != 1:
            raise CatalogValidationError(
                "schema_version: only integer version 1 is supported"
            )
        obj = record(
            root["package"],
            "id name summary category homepage install_specs detection tags documentation license vendor dependencies conflicts environment_guidance",
            "id name summary category homepage install_specs detection",
            "package",
        )
        category = string(obj["category"], "category")
        if category not in CATEGORIES:
            raise CatalogValidationError("category: unknown catalog category")
        return Package(
            id=string(obj["id"], "id"),
            name=string(obj["name"], "name"),
            summary=string(obj["summary"], "summary"),
            category=category,
            homepage=string(obj["homepage"], "homepage"),
            install_specs=tuple(
                install_spec(item)
                for item in array(obj["install_specs"], "install_specs")
            ),
            detection=tuple(
                detection(item) for item in array(obj["detection"], "detection")
            ),
            tags=strings(obj.get("tags", []), "tags"),
            documentation=optional_string(obj.get("documentation"), "documentation"),
            license=optional_string(obj.get("license"), "license"),
            vendor=optional_string(obj.get("vendor"), "vendor"),
            dependencies=tuple(
                dependency(item)
                for item in array(obj.get("dependencies", []), "dependencies")
            ),
            conflicts=strings(obj.get("conflicts", []), "conflicts"),
            environment_guidance=strings(
                obj.get("environment_guidance", []), "environment_guidance"
            ),
        )
    except CatalogValidationError:
        raise
    except (ValueError, TypeError) as error:
        # Do not echo arbitrary remote text or enum values into logs/errors.
        raise CatalogValidationError(
            "package: invalid domain field or mapping"
        ) from error
