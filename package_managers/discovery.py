"""Read-only native search formats. Remote text never becomes an install spec."""

import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import replace
from threading import Event

from models.discovery import LivePackage, ProviderBatch
from models.errors import ErrorCode
from system.commands import Command
from system.runner import CommandRunner

from .base import AdapterError, PackageManager
from .scoop import Scoop

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9+._@:/-]{0,255}")
_CAVEATS = (
    "Live-discovered metadata has not received curated catalog review.",
    "Version compatibility, dependencies, privileges and current installability are unverified.",
    "Browsing only: installation of live results is not enabled.",
)


def clean_text(value: str, limit: int = 1024) -> str:
    value = _ANSI.sub("", value)
    return " ".join(
        "".join(
            c if not unicodedata.category(c).startswith("C") else " " for c in value
        ).split()
    )[:limit]


def _format_error() -> AdapterError:
    return AdapterError(
        ErrorCode.VERIFICATION_FAILED,
        "Native search output has an unsupported or ambiguous format; no package identities were guessed.",
    )


class NativeSearchProvider:
    def __init__(
        self,
        adapter: PackageManager,
        runner: CommandRunner,
        *,
        brew_kind: str | None = None,
    ) -> None:
        self.adapter, self.runner, self.host = adapter, runner, adapter.host
        self.backend = adapter.id
        if brew_kind not in (None, "formula", "cask") or (
            brew_kind is not None and adapter.id != "homebrew"
        ):
            raise ValueError("invalid native search scope")
        self.brew_kind = brew_kind
        self.id = (
            ("homebrew-core" if brew_kind == "formula" else "homebrew-cask")
            if brew_kind
            else adapter.id
        )

    def command(self, query: str, timeout: float) -> Command:
        command = self.adapter.search(query)
        argv = command.argv
        if self.backend == "homebrew":
            if self.brew_kind is None:
                raise AdapterError(
                    ErrorCode.UNSUPPORTED_OPERATION,
                    "Select formula or cask search explicitly.",
                )
            argv = (argv[0], "search", "--" + self.brew_kind, "--", query)
        elif self.backend == "scoop":
            if not isinstance(self.adapter, Scoop):
                raise _format_error()
            command = self.adapter.search_json(query)
            argv = command.argv
        elif self.backend == "zypper":
            argv = (
                argv[0],
                "--non-interactive",
                "--no-refresh",
                "--xmlout",
                "search",
                "--details",
                "--type",
                "package",
                "--",
                query,
            )
        elif self.backend == "flatpak":
            argv = (
                argv[0],
                "search",
                "--user",
                "--columns=application:full,name:full,description:full,remotes:full",
                "--",
                query,
            )
        elif self.backend == "winget":
            argv += ("--count", "200")
        command = replace(
            command, argv=argv, timeout_seconds=min(timeout, command.timeout_seconds)
        )
        if command.requires_consent:
            raise AdapterError(
                ErrorCode.PERMISSION_DENIED,
                "Live search cannot run a privileged or mutating command.",
            )
        return command

    def search(
        self, query: str, cancel: Event, timeout_seconds: float
    ) -> ProviderBatch:
        if cancel.is_set():
            raise AdapterError(ErrorCode.CANCELLED, "Search was cancelled.")
        command = self.command(query, timeout_seconds)
        result = self.runner.run(command, cancel=cancel)
        if result.output_truncated:
            raise AdapterError(
                ErrorCode.VERIFICATION_FAILED,
                "Native output was truncated; incomplete package identities were discarded.",
            )
        if not result.succeeded:
            if (
                self.backend == "homebrew"
                and result.exit_code == 1
                and re.fullmatch(
                    r"Error: No (?:formulae|casks|formulae or casks) found for .+",
                    clean_text(result.stderr),
                )
            ):
                return ProviderBatch(())
            if (
                self.backend == "winget"
                and clean_text(result.stdout)
                == "No package found matching input criteria."
            ):
                return ProviderBatch(())
            if self.backend == "zypper" and result.exit_code == 104:
                batch = self.parse(result.stdout)
                if not batch.packages:
                    return batch
            error = result.error
            raise AdapterError(
                error.code if error else ErrorCode.COMMAND_FAILED,
                error.message
                if error
                else "Native search did not complete successfully.",
            )
        return self.parse(result.stdout)

    def _hit(
        self,
        source: str,
        native_id: str,
        name: str = "",
        summary: str = "",
        version: str | None = None,
        note: str = "",
    ) -> LivePackage:
        if _ID.fullmatch(native_id) is None or ".." in native_id or "…" in native_id:
            raise _format_error()
        return LivePackage(
            self.backend,
            source,
            native_id,
            clean_text(name or native_id, 256),
            clean_text(summary),
            self.host,
            clean_text(version, 256) if version else None,
            _CAVEATS + ((note,) if note else ()),
        )

    def parse(self, output: str) -> ProviderBatch:
        if len(output) > 65536:
            raise _format_error()
        # Strip terminal decoration, but preserve tabs/newlines used as record boundaries.
        output = _ANSI.sub("", output).replace("\r\n", "\n")
        try:
            if self.backend == "scoop":
                return self._scoop(output)
            if self.backend == "zypper":
                return self._zypper(output)
            if self.backend == "winget":
                return self._winget(output)
            hits: list[LivePackage] = []
            if self.backend == "homebrew":
                for line in output.splitlines():
                    if line.startswith("==>"):
                        if line.strip() != (
                            "==> Formulae"
                            if self.brew_kind == "formula"
                            else "==> Casks"
                        ):
                            raise _format_error()
                        continue
                    if not line.strip():
                        continue
                    for token in line.split():
                        if token == "✔":
                            continue
                        token = token.removesuffix("✔")
                        if (
                            re.fullmatch(
                                r"[a-z0-9][a-z0-9+._@-]*(?:/[a-z0-9][a-z0-9+._@-]*){0,2}",
                                token,
                            )
                            is None
                        ):
                            raise _format_error()
                        source = self.id
                        if token.count("/") == 2:
                            tap, native = token.rsplit("/", 1)
                            if tap == (
                                "homebrew/core"
                                if self.brew_kind == "formula"
                                else "homebrew/cask"
                            ):
                                token = native
                            else:
                                source = "homebrew-tap:" + tap
                        hits.append(
                            self._hit(
                                source,
                                token,
                                note="Native tap aliases and remote URLs have not been verified.",
                            )
                        )
            elif self.backend == "apt":
                for line in output.splitlines():
                    if not line.strip():
                        continue
                    match = re.fullmatch(r"([a-z0-9][a-z0-9+.:_-]*) - (.*)", line)
                    if not match:
                        raise _format_error()
                    hits.append(
                        self._hit(
                            "apt-configured",
                            match[1],
                            summary=match[2],
                            note="Search covers configured APT caches; repository origin is unknown, not necessarily Ubuntu noble.",
                        )
                    )
            elif self.backend == "pacman":
                current: tuple[str, str, str] | None = None
                description: list[str] = []
                for line in output.splitlines() + [""]:
                    if line.startswith((" ", "\t")) and current:
                        description.append(line.strip())
                        continue
                    if current:
                        hits.append(
                            self._hit(
                                "pacman:" + current[0],
                                current[1],
                                summary=" ".join(description),
                                version=current[2],
                                note="Repository alias and architecture compatibility are unverified.",
                            )
                        )
                        current, description = None, []
                    if line.strip():
                        match = re.fullmatch(
                            r"([A-Za-z0-9._-]+)/([a-z0-9][a-z0-9+._@-]*) (\S+)(?: .*)?",
                            line,
                        )
                        if not match:
                            raise _format_error()
                        current = match[1], match[2], match[3]
            elif self.backend == "dnf":
                for line in output.splitlines():
                    stripped = line.strip()
                    if (
                        not stripped
                        or stripped.startswith(
                            ("=", "Matched fields:", "Last metadata expiration check:")
                        )
                        or stripped == "No matches found."
                    ):
                        continue
                    match = re.fullmatch(
                        r"\s*([A-Za-z0-9][A-Za-z0-9+._-]*)\s+:\s+(.*)", line
                    )
                    if not match and "\t" in line:
                        match = re.fullmatch(
                            r"\s*([A-Za-z0-9][A-Za-z0-9+._-]*)\t(.*)", line
                        )
                    if not match:
                        raise _format_error()
                    hits.append(
                        self._hit(
                            "dnf-configured",
                            match[1],
                            summary=match[2],
                            note="Configured cached repositories and native architecture qualifiers are not installation verification.",
                        )
                    )
            elif self.backend == "apk":
                for line in output.splitlines():
                    if not line.strip():
                        continue
                    match = re.fullmatch(
                        r"([a-z0-9][a-z0-9+._-]*?)-([0-9][A-Za-z0-9._+~]*-r[0-9]+)",
                        line.strip(),
                    )
                    if not match:
                        raise _format_error()
                    hits.append(
                        self._hit(
                            "apk-configured",
                            match[1],
                            version=match[2],
                            note="Configured cache origin is unknown; this is not a verified Alpine main mapping.",
                        )
                    )
            elif self.backend == "flatpak":
                if output.strip() in {"No matches found", "No matches found."}:
                    return ProviderBatch(())
                for line in output.splitlines():
                    if not line.strip() or line.startswith("Application ID\t"):
                        continue
                    fields = line.split("\t")
                    if (
                        len(fields) != 4
                        or re.fullmatch(
                            r"[A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z][A-Za-z0-9_-]*){2,}",
                            fields[0],
                        )
                        is None
                    ):
                        raise _format_error()
                    for remote in fields[3].split(","):
                        remote = remote.strip()
                        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", remote) is None:
                            raise _format_error()
                        source = (
                            "flathub"
                            if remote == "flathub"
                            else "flatpak-user:" + remote
                        )
                        hits.append(
                            self._hit(
                                source,
                                fields[0],
                                fields[1],
                                fields[2],
                                note="Per-user remote alias only; URL, branch, permissions and installability are unverified.",
                            )
                        )
            else:
                raise AdapterError(
                    ErrorCode.UNSUPPORTED_OPERATION,
                    "This backend has no reviewed search parser.",
                )
            return ProviderBatch(tuple(hits[:200]), len(hits) > 200)
        except (
            ValueError,
            TypeError,
            KeyError,
            ET.ParseError,
            RecursionError,
        ) as error:
            if isinstance(error, AdapterError):
                raise
            raise _format_error() from error

    def _scoop(self, output: str) -> ProviderBatch:
        def pairs(entries: list[tuple[str, object]]) -> dict[str, object]:
            data: dict[str, object] = {}
            for key, value in entries:
                if key in data:
                    raise _format_error()
                data[key] = value
            return data

        try:
            data = json.loads(output, object_pairs_hook=pairs)
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("packages"), list)
                or type(data.get("limited")) is not bool
            ):
                raise _format_error()
            hits = []
            for row in data["packages"]:
                if (
                    not isinstance(row, dict)
                    or not isinstance(row.get("Name"), str)
                    or not isinstance(row.get("Source"), str)
                ):
                    raise _format_error()
                name, bucket = row["Name"], row["Source"]
                if (
                    re.fullmatch(r"[a-z0-9][a-z0-9._-]*", bucket) is None
                    or re.fullmatch(r"[a-z0-9][a-z0-9._-]*", name) is None
                ):
                    raise _format_error()
                version = row.get("Version")
                if version is not None and not isinstance(version, str):
                    raise _format_error()
                source = (
                    "scoop-" + bucket
                    if bucket in {"main", "extras"}
                    else "scoop-bucket:" + bucket
                )
                hits.append(
                    self._hit(
                        source,
                        name,
                        version=version,
                        note="Bucket remote/configuration is unverified; a reported bucket may not be installed locally. Native search semantics depend on Scoop configuration.",
                    )
                )
            return ProviderBatch(tuple(hits[:200]), data["limited"] or len(hits) > 200)
        except (ValueError, TypeError, RecursionError) as error:
            if isinstance(error, AdapterError):
                raise
            raise _format_error() from error

    def _zypper(self, output: str) -> ProviderBatch:
        if "<!DOCTYPE" in output.upper() or "<!ENTITY" in output.upper():
            raise _format_error()
        try:
            root = ET.fromstring(output)
            result = (
                root.find(".//search-result") if root.tag != "search-result" else root
            )
            if result is None or root.find(".//message[@type='error']") is not None:
                raise _format_error()
            hits = []
            for row in result.findall(".//solvable"):
                if row.get("kind") != "package":
                    continue
                repository = row.get("repository", "")
                if not repository or len(repository) > 200:
                    raise _format_error()
                hits.append(
                    self._hit(
                        "zypper:" + clean_text(repository, 200),
                        row.attrib["name"],
                        version=row.get("edition"),
                        note="Repository alias, architecture and configured URL are unverified.",
                    )
                )
            return ProviderBatch(tuple(hits[:200]), len(hits) > 200)
        except (ET.ParseError, KeyError, ValueError) as error:
            if isinstance(error, AdapterError):
                raise
            raise _format_error() from error

    def _winget(self, output: str) -> ProviderBatch:
        lines = output.splitlines()
        header = next(
            (
                n
                for n, line in enumerate(lines)
                if re.fullmatch(
                    r"Name\s+Id\s+Version(?:\s+Match)?(?:\s+Source)?\s*", line
                )
            ),
            None,
        )
        if (
            header is None
            or header + 1 >= len(lines)
            or re.fullmatch(r"-+\s*", lines[header + 1]) is None
        ):
            raise _format_error()
        labels = lines[header]
        id_start, version_start = labels.index("Id"), labels.index("Version")
        end_version = min(
            (labels.index(name) for name in ("Match", "Source") if name in labels),
            default=len(labels),
        )
        hits = []
        for line in lines[header + 2 :]:
            if not line.strip():
                continue
            # WinGet aligns display cells. Refuse wide/combining-name rows rather
            # than slicing a shortened, still-syntactically-valid native ID.
            if any(
                unicodedata.east_asian_width(c) in {"W", "F"}
                or unicodedata.combining(c)
                for c in line
            ):
                raise _format_error()
            if len(line) < version_start or not line[:id_start].strip():
                raise _format_error()
            native_id = line[id_start:version_start].strip()
            version = (
                line[version_start:end_version].strip()
                if end_version < len(labels)
                else line[version_start:].strip()
            )
            if (
                re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", native_id) is None
                or ".." in native_id
            ):
                raise _format_error()
            hits.append(
                self._hit(
                    "winget",
                    native_id,
                    line[:id_start].strip(),
                    version=version if version and "…" not in version else None,
                    note="WinGet source alias was selected; configured source URL and installer compatibility are unverified.",
                )
            )
        return ProviderBatch(tuple(hits[:200]), len(hits) >= 200)


def providers_for(
    adapters: tuple[PackageManager, ...], runner: CommandRunner
) -> tuple[NativeSearchProvider, ...]:
    providers: list[NativeSearchProvider] = []
    for adapter in adapters:
        if adapter.id == "homebrew":
            providers.extend(
                NativeSearchProvider(adapter, runner, brew_kind=kind)
                for kind in ("formula", "cask")
            )
        elif adapter.id in {
            "winget",
            "scoop",
            "apt",
            "pacman",
            "dnf",
            "zypper",
            "apk",
            "flatpak",
        }:
            providers.append(NativeSearchProvider(adapter, runner))
    return tuple(providers)
