"""Fail-closed native readers for APT and Homebrew; no shell or registry guesses.

Other command adapters deliberately need a reviewed inspection implementation before
the application can orchestrate mutations through them.
"""

import hashlib
import json
import re
from dataclasses import replace
from threading import Event
from typing import Any

from models.errors import ErrorCode
from models.inventory import InstalledPackage, Inventory, NativeReview
from models.package import PackageVersion
from models.planning import OperationAction
from models.specs import InstallSpec
from system.commands import Command
from system.runner import CommandRunner

from .apt import APT
from .base import AdapterError, BackendAction, PackageManager
from .homebrew import Homebrew


def _fail(message: str) -> AdapterError:
    return AdapterError(ErrorCode.VERIFICATION_FAILED, message)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _string(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise _fail("Native metadata contains an invalid required string.")
    return value


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _fail("Native metadata has an unsupported object format.")
    return value


def _array(value: object) -> list[Any]:
    if not isinstance(value, list):
        raise _fail("Native metadata has an unsupported array format.")
    return value


class NativeInspector:
    def __init__(self, adapter: PackageManager, runner: CommandRunner) -> None:
        self.adapter = adapter
        self.runner = runner

    def read(self, command: Command, cancel: Event | None) -> str:
        if command.requires_consent:
            raise _fail("Inspection cannot run a privileged or mutating command.")
        result = self.runner.run(command, cancel=cancel)
        if result.error:
            raise AdapterError(result.error.code, result.error.message)
        if not result.succeeded or result.output_truncated:
            raise _fail("Native inspection failed or returned incomplete output.")
        return result.stdout

    def json(self, command: Command, cancel: Event | None) -> dict[str, Any]:
        def pairs(entries: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in entries:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result

        try:
            return _object(
                json.loads(self.read(command, cancel), object_pairs_hook=pairs)
            )
        except (ValueError, RecursionError) as error:
            if isinstance(error, AdapterError):
                raise
            raise _fail("Native JSON could not be parsed safely.") from error


class HomebrewInspector(NativeInspector):
    def __init__(self, adapter: Homebrew, runner: CommandRunner) -> None:
        super().__init__(adapter, runner)

    def inventory(self, cancel: Event | None = None) -> Inventory:
        command = self.adapter.list_installed()
        command = replace(
            command, argv=(command.argv[0], "info", "--json=v2", "--installed")
        )
        data = self.json(command, cancel)
        packages = []
        for kind in ("formulae", "casks"):
            for raw in _array(data.get(kind)):
                record = _object(raw)
                name = _string(record.get("name" if kind == "formulae" else "token"))
                tap = _string(record.get("tap"))
                source = {
                    "homebrew/core": "homebrew-core",
                    "homebrew/cask": "homebrew-cask",
                }.get(tap, "unreviewed-homebrew-tap")
                if kind == "formulae":
                    versions = tuple(
                        _string(_object(keg).get("version"))
                        for keg in _array(record.get("installed"))
                    )
                else:
                    installed = record.get("installed")
                    versions = () if installed is None else (_string(installed),)
                if versions:
                    packages.append(
                        InstalledPackage(name, tuple(dict.fromkeys(versions)), source)
                    )
        try:
            return Inventory(self.adapter.id, tuple(packages))
        except ValueError as error:
            raise _fail(
                "Ambiguous Homebrew inventory; no absence inference is safe."
            ) from error

    def review(
        self, spec: InstallSpec, action: OperationAction, cancel: Event | None = None
    ) -> NativeReview:
        data = self.json(self.adapter.show(spec), cancel)
        cask = spec.source.id == "homebrew-cask"
        kind = "casks" if cask else "formulae"
        records = _array(data.get(kind))
        if len(records) != 1 or _array(data.get("formulae" if cask else "casks")):
            raise _fail("Homebrew did not resolve exactly one selected package.")
        record = _object(records[0])
        if record.get("token" if cask else "name") != spec.native_id or record.get(
            "tap"
        ) != ("homebrew/cask" if cask else "homebrew/core"):
            raise _fail(
                "Resolved Homebrew identity or tap differs from the reviewed source."
            )
        if record.get("disabled") or record.get("deprecated"):
            raise AdapterError(
                ErrorCode.UNSUPPORTED_OPERATION,
                "Disabled or deprecated Homebrew packages require separate review.",
            )
        version = _string(
            record.get("version")
            if cask
            else _object(record.get("versions")).get("stable")
        )
        if not cask:
            revision = record.get("revision", 0)
            if type(revision) is not int or revision < 0:
                raise _fail("Homebrew revision is invalid.")
            if revision:
                version += f"_{revision}"
        # Remove installation-local fields so execution revalidation tolerates an
        # earlier approved item satisfying a dependency. Everything else is evidence.
        stable = {
            k: v
            for k, v in record.items()
            if k
            not in {
                "installed",
                "linked_keg",
                "outdated",
                "pinned",
                "installed_time",
                "installed_as_dependency",
                "installed_on_request",
            }
        }
        changes: tuple[str, ...]
        if action == OperationAction.UNINSTALL:
            changes = (
                f"Remove selected Homebrew package {spec.native_id}; native uninstall hooks may run.",
            )
        else:
            dependencies = (
                record.get("depends_on", {}) if cask else record.get("dependencies", [])
            )
            changes = (
                f"{action.value} {spec.native_id} at native version {version} from {spec.source.id}.",
                "Homebrew resolves native dependencies and installer hooks; this is not an exact transaction simulation.",
                "Native dependency metadata: "
                + json.dumps(dependencies, sort_keys=True, separators=(",", ":")),
                "Native platform requirements: "
                + json.dumps(
                    record.get("depends_on", {})
                    if cask
                    else record.get("requirements", []),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        return NativeReview(
            None if action == OperationAction.UNINSTALL else version,
            _digest(stable),
            changes,
        )


class APTInspector(NativeInspector):
    def __init__(self, adapter: APT, runner: CommandRunner) -> None:
        super().__init__(adapter, runner)

    def inventory(self, cancel: Event | None = None) -> Inventory:
        output = self.read(self.adapter.list_installed(), cancel)
        packages = []
        for line in output.splitlines():
            fields = line.split("\t")
            if len(fields) != 3:
                raise _fail("DPKG inventory has an unsupported row format.")
            name, version, status = fields
            if re.fullmatch(r"[a-z0-9][a-z0-9+.-]*(?::[a-z0-9]+)?", name) is None:
                raise _fail("DPKG returned an invalid native package identifier.")
            if status == "installed":
                # Only matching-architecture packages may satisfy an unqualified ID.
                architecture = {"x86_64": "amd64", "arm64": "arm64"}.get(
                    self.adapter.host.architecture.value
                )
                aliases: tuple[str, ...] = ()
                if ":" in name and name.rsplit(":", 1)[1] == architecture:
                    aliases = (name,)
                    name = name.rsplit(":", 1)[0]
                elif ":" not in name and architecture:
                    aliases = (name + ":" + architecture,)
                packages.append(
                    InstalledPackage(name, (_string(version),), aliases=aliases)
                )
            elif status not in {"not-installed", "config-files"}:
                raise _fail(
                    "DPKG has partially installed packages; repair natively before planning."
                )
        try:
            return Inventory(self.adapter.id, tuple(packages))
        except ValueError as error:
            raise _fail("DPKG inventory is ambiguous.") from error

    def _command(self, argv: tuple[str, ...], spec: InstallSpec) -> Command:
        return self.adapter.request(
            argv, BackendAction.SHOW, spec, environment=(("LC_ALL", "C"),)
        )

    def review(
        self, spec: InstallSpec, action: OperationAction, cancel: Event | None = None
    ) -> NativeReview:
        self.adapter.validate_spec(BackendAction.SHOW, spec)
        available = self.adapter.availability()
        if not available.available:
            raise AdapterError(
                ErrorCode.MISSING_BACKEND,
                "APT inspection needs its native executables.",
            )
        paths = dict(available.executables)
        version = None
        evidence = "remove"
        if action != OperationAction.UNINSTALL:
            # Pick only the selected release, not the user's unrelated default candidate.
            policy_args = (
                paths["apt-cache"],
                "-o",
                "APT::Default-Release=noble",
                "policy",
                "--",
                spec.native_id,
            )
            policy = self.read(self._command(policy_args, spec), cancel)
            matches = re.findall(r"^\s*Candidate:\s*(\S+)\s*$", policy, re.MULTILINE)
            if len(matches) != 1 or matches[0] == "(none)":
                raise _fail("APT has no unambiguous selected-release candidate.")
            version = spec.version.value if spec.version else matches[0]
            selected = replace(spec, version=PackageVersion(version))
            metadata = self.read(self.adapter.show(selected), cancel)
            paragraphs = [p for p in metadata.strip().split("\n\n") if p.strip()]
            if len(paragraphs) != 1:
                raise _fail(
                    "APT metadata did not resolve a single exact package version."
                )
            fields = dict(
                re.findall(r"^([A-Za-z0-9-]+): ([^\n]*)$", paragraphs[0], re.MULTILINE)
            )
            if (
                fields.get("Package") != spec.native_id.split(":")[0]
                or fields.get("Version") != version
            ):
                raise _fail("APT resolved a different package or version.")
            # `apt-cache policy` exposes the configured native origins. Match the
            # candidate's repository rows against release metadata, not a hostname guess.
            origins = self.read(
                self._command((paths["apt-cache"], "policy"), spec), cancel
            )
            allowed = set()
            lines = origins.splitlines()
            for index, line in enumerate(lines[:-1]):
                entry = re.fullmatch(
                    r"\s*\d+\s+(\S+)\s+(\S+)\s+(\S+)\s+Packages\s*", line
                )
                if (
                    entry
                    and re.search(
                        r"(?:^|,)o=Ubuntu(?:,|$)",
                        lines[index + 1].strip().removeprefix("release "),
                    )
                    and re.search(
                        r"(?:^|,)n=noble(?:,|$)",
                        lines[index + 1].strip().removeprefix("release "),
                    )
                ):
                    allowed.add(entry.groups())
            in_version = False
            repositories = []
            for line in policy.splitlines():
                header = re.fullmatch(r"\s*(?:\*\*\*\s+)?(\S+)\s+\d+\s*", line)
                if header:
                    in_version = header[1] == version
                row = re.fullmatch(
                    r"\s*\d+\s+(\S+)\s+(\S+)\s+(\S+)\s+Packages\s*", line
                )
                if in_version and row:
                    repositories.append(row.groups())
            if not repositories or any(row not in allowed for row in repositories):
                raise _fail(
                    "Selected APT version is not exclusively mapped to reviewed Ubuntu noble origins."
                )
            evidence = metadata + json.dumps(sorted(repositories))
        token = spec.native_id + (f"={version}" if version else "")
        args = (
            ("remove",)
            if action == OperationAction.UNINSTALL
            else ("install", "--only-upgrade")
            if action == OperationAction.UPGRADE
            else ("install",)
        )
        simulation = (
            paths["apt-get"],
            "--simulate",
            "--assume-no",
            "--target-release",
            "noble",
            *args,
            "--",
            token,
        )
        output = self.read(self._command(simulation, spec), cancel)
        effects = []
        for line in output.splitlines():
            if line.startswith(("Inst ", "Remv ", "Conf ")):
                if (
                    re.fullmatch(
                        r"(?:Inst|Remv|Conf) [a-z0-9][a-z0-9+.:_-]*(?: .*?)?", line
                    )
                    is None
                ):
                    raise _fail(
                        "APT simulation contains an unsupported transaction row."
                    )
                effects.append(line)
            if line.startswith(("Purg ", "E:")):
                raise _fail(
                    "APT simulation proposes unsupported effects or reports an error."
                )
        return NativeReview(version, _digest(evidence), tuple(dict.fromkeys(effects)))
