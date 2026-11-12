"""Opt-in support facts with no usernames, paths, credentials or process output."""

import json
import platform
import re
from dataclasses import dataclass
from pathlib import Path

from models.platform import Platform
from package_managers.base import Availability


@dataclass(frozen=True)
class DiagnosticReport:
    app_version: str
    python_version: str
    os: str
    architecture: str
    distribution: str | None
    distribution_version: str | None
    backends: tuple[tuple[str, bool], ...]

    def __post_init__(self) -> None:
        def safe(value: str, pattern: str) -> bool:
            return (
                isinstance(value, str)
                and len(value) <= 64
                and re.fullmatch(pattern, value) is not None
            )

        if not safe(
            self.app_version, r"[0-9]+\.[0-9]+\.[0-9]+(?:\.dev[0-9]+)?"
        ) or not safe(
            self.python_version, r"[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?"
        ):
            raise ValueError("diagnostic versions must be bounded version identifiers")
        if self.os not in {
            "windows",
            "macos",
            "linux",
            "unknown",
        } or self.architecture not in {"x86_64", "arm64", "x86", "armv7", "unknown"}:
            raise ValueError("diagnostic platform is unsupported")
        for value in (self.distribution, self.distribution_version):
            if value is not None and not safe(value, r"[A-Za-z0-9][A-Za-z0-9._+-]*"):
                raise ValueError("diagnostic distribution must be a bounded identifier")
        if not isinstance(self.backends, tuple) or len(self.backends) > 32:
            raise ValueError("diagnostic backend list exceeds its bound")
        for backend, available in self.backends:
            if (
                not safe(backend, r"[a-z0-9][a-z0-9._-]*")
                or type(available) is not bool
            ):
                raise ValueError("diagnostic backend facts must be typed identifiers")


def collect_diagnostics(
    app_version: str, host: Platform, availability: tuple[Availability, ...] = ()
) -> DiagnosticReport:
    if (
        not isinstance(app_version, str)
        or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:\.dev[0-9]+)?", app_version) is None
    ):
        raise ValueError("diagnostics require a bounded application version")
    if len(app_version) > 64 or len(availability) > 32:
        raise ValueError("diagnostic input exceeds its bound")
    return DiagnosticReport(
        app_version,
        platform.python_version(),
        host.os.value,
        host.architecture.value,
        host.distribution.id if host.distribution else None,
        host.distribution.version if host.distribution else None,
        tuple((item.backend, item.available) for item in availability),
    )


def diagnostic_json(report: DiagnosticReport) -> str:
    if not isinstance(report, DiagnosticReport):
        raise ValueError("a diagnostic report is required")
    # Do not accept arbitrary dictionaries or serialize adapter objects: their
    # executable locations/reasons may contain local configuration.
    payload = {
        "schema_version": 1,
        "application": {"version": report.app_version},
        "python": report.python_version,
        "platform": {
            "os": report.os,
            "architecture": report.architecture,
            "distribution": report.distribution,
            "distribution_version": report.distribution_version,
        },
        "backends": [
            {"id": backend, "available": available}
            for backend, available in report.backends
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    if len(encoded.encode("utf-8")) > 16384:
        raise ValueError("diagnostic report exceeds its export bound")
    return encoded


def export_diagnostics(report: DiagnosticReport, path: Path) -> None:
    # Exclusive creation never overwrites a user's unrelated diagnostic/file.
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(diagnostic_json(report))
