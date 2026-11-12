"""Portable collections and explicit project-local file changes."""

import hashlib
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from threading import Event
from typing import Any, Callable, ParamSpec, TypeVar
from uuid import uuid4

from models.environment import (
    ConfigPlan,
    EnvironmentChoice,
    Manifest,
    ManifestPackage,
    ProjectConfigChoice,
    RuntimeRequest,
)
from models.errors import ErrorCode
from models.package import PackageVersion
from package_managers.base import AdapterError
from services.planning import Planner
from services.records import PackageRequest, ReviewedPlan

LIMIT = 262144


class EnvironmentError(AdapterError):
    """Expected, safe-to-display refusal; never includes file contents or paths."""

    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.UNSUPPORTED_OPERATION, message)


P = ParamSpec("P")
T = TypeVar("T")


def _validated(function: Callable[P, T]) -> Callable[P, T]:
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return function(*args, **kwargs)
        except EnvironmentError:
            raise
        except (ValueError, TypeError, RecursionError) as exc:
            raise EnvironmentError(
                "Invalid environment data; review the supported schema, exact versions and project paths."
            ) from exc

    return wrapped


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise EnvironmentError("Duplicate manifest keys are unsupported.")
        result[key] = value
    return result


def _fields(value: object, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise EnvironmentError("Manifest fields do not match schema version 1.")
    return value


@_validated
def load_manifest(path: Path) -> Manifest:
    with path.open("rb") as stream:
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise EnvironmentError("Manifest exceeds 256 KiB.")
    try:
        obj = _fields(
            json.loads(raw, object_pairs_hook=_object),
            {
                "schema_version",
                "name",
                "description",
                "packages",
                "runtimes",
                "configurations",
                "environment",
            },
        )
        if type(obj["schema_version"]) is not int or obj["schema_version"] != 1:
            raise EnvironmentError("Unsupported manifest schema version.")
        for key in ("packages", "runtimes", "configurations", "environment"):
            if not isinstance(obj[key], list) or len(obj[key]) > 200:
                raise EnvironmentError("Manifest collections must be bounded arrays.")
        packages = []
        for value in obj["packages"]:
            item = _fields(value, {"id", "version"})
            version = item["version"]
            if version is not None:
                v = _fields(version, {"value", "channel"})
                version = PackageVersion(v["value"], v["channel"])
            packages.append(ManifestPackage(item["id"], version))
        return Manifest(
            obj["name"],
            tuple(packages),
            tuple(
                RuntimeRequest(**_fields(v, {"manager", "runtime", "version"}))
                for v in obj["runtimes"]
            ),
            obj["description"],
            tuple(
                ProjectConfigChoice(**_fields(v, {"kind", "version"}))
                for v in obj["configurations"]
            ),
            tuple(
                EnvironmentChoice(**_fields(v, {"name", "relative_path"}))
                for v in obj["environment"]
            ),
        )
    except (TypeError, KeyError, RecursionError, UnicodeError) as exc:
        raise EnvironmentError("Invalid portable manifest.") from exc


def save_manifest(manifest: Manifest, path: Path) -> None:
    obj = {
        "schema_version": 1,
        "name": manifest.name,
        "description": manifest.description,
        "packages": [
            {
                "id": p.package_id,
                "version": None
                if p.version is None
                else {"value": p.version.value, "channel": p.version.channel},
            }
            for p in manifest.packages
        ],
        "runtimes": [
            {"manager": r.manager, "runtime": r.runtime, "version": r.version}
            for r in manifest.runtimes
        ],
        "configurations": [
            {"kind": c.kind, "version": c.version} for c in manifest.configurations
        ],
        "environment": [
            {"name": e.name, "relative_path": e.relative_path}
            for e in manifest.environment
        ],
    }
    raw = json.dumps(obj, indent=2, ensure_ascii=True) + "\n"
    if len(raw.encode()) > LIMIT:
        raise EnvironmentError("Manifest exceeds 256 KiB.")
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(raw)


@dataclass(frozen=True)
class ManifestPreview:
    manifest: Manifest
    reviewed: ReviewedPlan
    unsupported: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.unsupported and self.reviewed.plan.ready


def preview_manifest(
    manifest: Manifest, planner: Planner, cancel: Event | None = None
) -> ManifestPreview:
    reviewed = planner.preview(
        tuple(
            PackageRequest(p.package_id, version=p.version) for p in manifest.packages
        ),
        cancel=cancel,
    )
    unsupported = [issue.message for issue in reviewed.plan.issues]
    if manifest.runtimes:
        unsupported.append(
            "Runtime selections require separate native-manager review; package installation does not execute them."
        )
    if manifest.configurations or manifest.environment:
        unsupported.append(
            "Project configuration requires a selected local project and separate file consent."
        )
    return ManifestPreview(manifest, reviewed, tuple(unsupported))


def starter_presets() -> tuple[Manifest, ...]:
    rows = (
        ("General Developer", ("git", "vscode", "ripgrep"), "Reviewed everyday tools."),
        (
            "C/C++ Developer",
            ("git", "gcc", "cmake", "vscode"),
            "Compiler and build tools; debugger and platform SDK selection remain separate.",
        ),
        (
            "Python Developer",
            ("git", "python", "uv", "vscode"),
            "Python and uv; project dependencies are managed separately.",
        ),
        (
            "Backend Developer",
            ("git", "nodejs", "postgresql"),
            "Backend starter subset; database initialization and credentials remain separate.",
        ),
        (
            "Frontend Developer",
            ("git", "nodejs", "vscode"),
            "Node.js starter subset; framework tooling remains separate.",
        ),
        (
            "Java/Spring Developer",
            ("git", "java", "vscode"),
            "JDK starter subset; Maven, Gradle and Spring tooling are not yet curated.",
        ),
        (
            "Flutter/Mobile Developer",
            ("git", "android-platform-tools", "vscode"),
            "Mobile starter subset; Flutter, full Android SDK and Xcode are not included.",
        ),
        (
            "Data Science / ML",
            ("git", "python", "uv"),
            "Python starter subset; Jupyter, scientific libraries and GPU drivers are not included.",
        ),
        (
            "DevOps / Cloud",
            ("git", "github-cli"),
            "Cloud starter subset; provider CLIs and container platform selection remain separate.",
        ),
        (
            "Systems Programming",
            ("git", "gcc", "cmake", "ripgrep"),
            "Systems starter subset; Rust toolchains require separate runtime-manager review.",
        ),
    )
    return tuple(
        Manifest(
            name, tuple(ManifestPackage(p) for p in packages), description=description
        )
        for name, packages, description in rows
    )


HEADER = "# Managed by Dev Setup (project-local)\n"


def _safe(path: Path) -> None:
    for item in (path, *path.parents):
        if item.exists() or item.is_symlink():
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(
                info, "st_file_attributes", 0
            ) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise EnvironmentError(
                    "Project configuration refuses symbolic links and reparse points."
                )
            if item == path and stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                raise EnvironmentError(
                    "Project configuration refuses hard-linked files."
                )


def _read(path: Path) -> tuple[str | None, str | None]:
    _safe(path)
    if not path.exists():
        return None, None
    with path.open("rb") as stream:
        data = stream.read(65537)
    if len(data) > 65536:
        raise EnvironmentError("Existing project configuration exceeds its size limit.")
    return hashlib.sha256(data).hexdigest(), data.decode("utf-8")


class ProjectConfigService:
    def __init__(self) -> None:
        self._plans: dict[str, ConfigPlan] = {}

    def _preview(
        self,
        project: Path,
        filename: str,
        content: str,
        digest: str | None,
        previous: str | None,
        notes: tuple[str, ...] = (),
    ) -> ConfigPlan:
        plan = ConfigPlan(
            str(uuid4()), project, project / filename, content, digest, previous, notes
        )
        self._plans[plan.id] = plan
        return plan

    @staticmethod
    def _project(project: Path) -> Path:
        project = project.absolute()
        _safe(project)
        if not project.is_dir():
            raise EnvironmentError("Select an existing project directory.")
        return project

    @_validated
    def preview(self, project: Path, kind: str, version: str) -> ConfigPlan:
        choice = ProjectConfigChoice(kind, version)
        project = self._project(project)
        filename = {
            "python": ".python-version",
            "rust": "rust-toolchain.toml",
            "mise-python": "mise.toml",
            "mise-node": "mise.toml",
        }[choice.kind]
        digest, previous = _read(project / filename)
        if kind == "python":
            if previous is not None:
                ProjectConfigChoice("python", previous.strip())
            content = version + "\n"
        elif kind == "rust":
            if previous is not None:
                old = self._managed(previous, "[toolchain]\n", {"channel", "profile"})
                if set(old) != {"channel", "profile"} or old["profile"] != "minimal":
                    raise EnvironmentError(
                        "Existing Rust configuration contains unmanaged settings."
                    )
                ProjectConfigChoice("rust", old["channel"])
            content = (
                HEADER
                + "[toolchain]\nchannel = "
                + json.dumps(version)
                + '\nprofile = "minimal"\n'
            )
        else:
            values = self._managed(previous, "[tools]\n", {"python", "node"})
            for key, value in values.items():
                ProjectConfigChoice(f"mise-{key}", value)
            values[kind.removeprefix("mise-")] = version
            content = (
                HEADER
                + "[tools]\n"
                + "".join(
                    f"{key} = {json.dumps(value)}\n"
                    for key, value in sorted(values.items())
                )
            )
        return self._preview(
            project,
            filename,
            content,
            digest,
            previous,
            (
                "Changes only the reviewed project file; runtime installation is separate.",
            ),
        )

    @staticmethod
    def _managed(
        previous: str | None, section: str, allowed: set[str]
    ) -> dict[str, str]:
        if previous is None:
            return {}
        if not previous.startswith(HEADER + section):
            raise EnvironmentError(
                "Existing configuration is not managed by Dev Setup."
            )
        values = {}
        for line in previous[len(HEADER + section) :].splitlines():
            key, sep, raw = line.partition(" = ")
            if not sep or key not in allowed or key in values:
                raise EnvironmentError(
                    "Existing configuration contains unmanaged settings."
                )
            value = json.loads(raw)
            if not isinstance(value, str):
                raise EnvironmentError(
                    "Existing configuration contains unmanaged settings."
                )
            values[key] = value
        return values

    @_validated
    def preview_environment(
        self, project: Path, name: str, relative_path: str
    ) -> ConfigPlan:
        choice = EnvironmentChoice(name, relative_path)
        project = self._project(project)
        _safe(project / choice.relative_path)
        digest, previous = _read(project / ".dev-setup.env")
        values = self._managed(
            previous, "", {"JAVA_HOME", "PYTHONPATH", "GOPATH", "CARGO_HOME"}
        )
        for key, value in values.items():
            EnvironmentChoice(key, value)
        values[name] = relative_path
        content = HEADER + "".join(
            f"{key} = {json.dumps(value)}\n" for key, value in sorted(values.items())
        )
        return self._preview(
            project,
            ".dev-setup.env",
            content,
            digest,
            previous,
            (
                "Declarative project path choices only; this file is not automatically loaded into any shell or process.",
            ),
        )

    @_validated
    def apply(self, plan: ConfigPlan, confirmed: bool) -> Path:
        if confirmed is not True or self._plans.get(plan.id) != plan:
            raise EnvironmentError(
                "Applying configuration requires approval of an exact preview from this service."
            )
        self._project(plan.project)
        if (
            plan.path.parent != plan.project
            or _read(plan.path)[0] != plan.previous_digest
        ):
            raise EnvironmentError(
                "Project configuration changed; review a fresh preview."
            )
        # Recheck declared relative paths immediately before writing.
        if plan.path.name == ".dev-setup.env":
            for value in self._managed(
                plan.content, "", {"JAVA_HOME", "PYTHONPATH", "GOPATH", "CARGO_HOME"}
            ).values():
                _safe(plan.project / value)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=plan.project, delete=False
            ) as stream:
                temporary = stream.name
                stream.write(plan.content)
                stream.flush()
                os.fsync(stream.fileno())
            if _read(plan.path)[0] != plan.previous_digest:
                raise EnvironmentError(
                    "Project configuration changed during write; review again."
                )
            os.replace(temporary, plan.path)
            temporary = None
            del self._plans[plan.id]
            return plan.path
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
