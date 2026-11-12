"""Runtime and portable-environment records, independent of Qt."""

import re
from dataclasses import dataclass, field
from pathlib import Path

from models._validation import identifier, text
from models.errors import OperationError
from models.operations import OperationState
from models.package import PackageVersion
from system.commands import Command, CommandResult


def exact_version(value: str) -> None:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[._+-][A-Za-z0-9]+)*", value) is None
        or len(value) > 64
    ):
        raise ValueError(
            "Select an exact numeric runtime version; paths, aliases and option syntax are unsupported."
        )


@dataclass(frozen=True)
class RuntimeRequest:
    manager: str
    runtime: str
    version: str

    def __post_init__(self) -> None:
        identifier(self.manager, "runtime manager")
        identifier(self.runtime, "runtime name")
        exact_version(self.version)


@dataclass(frozen=True)
class RuntimePlan:
    id: str
    request: RuntimeRequest
    command: Command | None
    installed: tuple[str, ...]
    notes: tuple[str, ...]
    skip_reason: str | None = None

    def __post_init__(self) -> None:
        identifier(self.id, "runtime plan")
        if not isinstance(self.request, RuntimeRequest):
            raise ValueError("Runtime plans require a typed request.")
        if self.command is not None and (
            not isinstance(self.command, Command)
            or not self.command.mutates
            or self.command.source_id != f"runtime-{self.request.manager}"
        ):
            raise ValueError(
                "Runtime plan commands must retain their adapter source and mutation identity."
            )
        if not isinstance(self.installed, tuple) or len(set(self.installed)) != len(
            self.installed
        ):
            raise ValueError(
                "Runtime inventory must be an immutable set of exact versions."
            )
        for version in self.installed:
            exact_version(version)
        if not isinstance(self.notes, tuple):
            raise ValueError("Runtime notes must be immutable.")
        for note in self.notes:
            text(note, "runtime review note")
        if self.skip_reason is not None:
            text(self.skip_reason, "runtime skip reason")
            if self.command is not None:
                raise ValueError(
                    "Already-installed runtimes cannot carry an installation command."
                )

    @property
    def ready(self) -> bool:
        return self.command is not None or self.skip_reason is not None


@dataclass(frozen=True)
class RuntimeConsent:
    plan: RuntimePlan = field(repr=False)
    confirmed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.plan, RuntimePlan) or type(self.confirmed) is not bool:
            raise ValueError(
                "Runtime consent requires an exact plan and explicit decision."
            )


@dataclass(frozen=True)
class RuntimeResult:
    state: OperationState
    message: str
    command_result: CommandResult | None = field(default=None, repr=False)
    error: OperationError | None = None


@dataclass(frozen=True)
class ManifestPackage:
    package_id: str
    version: PackageVersion | None = None

    def __post_init__(self) -> None:
        identifier(self.package_id, "manifest package")
        if self.version is not None and not isinstance(self.version, PackageVersion):
            raise ValueError("Manifest versions require a PackageVersion.")


@dataclass(frozen=True)
class ProjectConfigChoice:
    kind: str
    version: str

    def __post_init__(self) -> None:
        if self.kind not in {"python", "rust", "mise-python", "mise-node"}:
            raise ValueError("Unsupported project configuration kind.")
        exact_version(self.version)


@dataclass(frozen=True)
class EnvironmentChoice:
    name: str
    relative_path: str

    def __post_init__(self) -> None:
        if self.name not in {"JAVA_HOME", "PYTHONPATH", "GOPATH", "CARGO_HOME"}:
            raise ValueError(
                "Only reviewed, non-secret project path variables are supported."
            )
        if (
            not isinstance(self.relative_path, str)
            or len(self.relative_path) > 200
            or re.fullmatch(r"\.?/?[A-Za-z0-9_./-]+", self.relative_path) is None
        ):
            raise ValueError("Environment values must be relative project paths.")
        if self.relative_path.startswith("/") or ".." in self.relative_path.split("/"):
            raise ValueError("Environment paths must stay within the selected project.")


@dataclass(frozen=True)
class Manifest:
    name: str
    packages: tuple[ManifestPackage, ...]
    runtimes: tuple[RuntimeRequest, ...] = ()
    description: str = ""
    configurations: tuple[ProjectConfigChoice, ...] = ()
    environment: tuple[EnvironmentChoice, ...] = ()

    def __post_init__(self) -> None:
        text(self.name, "manifest name")
        if not isinstance(self.description, str):
            raise ValueError("Manifest description must be text.")
        if len(self.name) > 100 or len(self.description) > 2000:
            raise ValueError("Manifest text exceeds its limit.")
        if self.description:
            text(self.description, "manifest description")
        for values, expected in (
            (self.packages, ManifestPackage),
            (self.runtimes, RuntimeRequest),
            (self.configurations, ProjectConfigChoice),
            (self.environment, EnvironmentChoice),
        ):
            if (
                not isinstance(values, tuple)
                or len(values) > 200
                or any(not isinstance(value, expected) for value in values)
            ):
                raise ValueError(
                    "Manifest collections must be bounded immutable typed records."
                )
        for keys in (
            tuple(p.package_id for p in self.packages),
            tuple((r.manager, r.runtime) for r in self.runtimes),
            tuple(c.kind for c in self.configurations),
            tuple(e.name for e in self.environment),
        ):
            if len(set(keys)) != len(keys):
                raise ValueError("Duplicate manifest entries are unsupported.")


@dataclass(frozen=True)
class ConfigPlan:
    id: str
    project: Path = field(repr=False)
    path: Path = field(repr=False)
    content: str = field(repr=False)
    previous_digest: str | None
    previous_content: str | None = field(repr=False)
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        identifier(self.id, "configuration plan")
        if (
            not isinstance(self.project, Path)
            or not isinstance(self.path, Path)
            or not self.project.is_absolute()
            or self.path.parent != self.project
        ):
            raise ValueError(
                "Configuration paths must stay directly inside an absolute project directory."
            )
        if self.path.name not in {
            ".python-version",
            "rust-toolchain.toml",
            "mise.toml",
            ".dev-setup.env",
        }:
            raise ValueError("Configuration plan filename is not reviewed.")
        for content in (self.content, self.previous_content):
            if content is not None and (
                not isinstance(content, str) or len(content.encode("utf-8")) > 65536
            ):
                raise ValueError("Configuration contents must be bounded text.")
        if (self.previous_digest is None) != (self.previous_content is None) or (
            self.previous_digest is not None
            and re.fullmatch(r"[0-9a-f]{64}", self.previous_digest) is None
        ):
            raise ValueError(
                "Previous configuration content requires a SHA-256 digest."
            )
        if not isinstance(self.notes, tuple):
            raise ValueError("Configuration notes must be immutable.")
        for note in self.notes:
            text(note, "configuration review note")
