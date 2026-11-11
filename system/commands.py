"""Typed process requests, display redaction, and command-bound consent."""

import math
import re
from dataclasses import dataclass, field
from datetime import datetime

from models._validation import text
from models.errors import OperationError
from models.planning import CommandPreview, aware_timestamp, utc_now
from models.platform import OperatingSystem
from models.specs import PrivilegeRequirement


@dataclass(frozen=True)
class Redactor:
    secrets: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.secrets, tuple) or any(
            not isinstance(item, str) or not item for item in self.secrets
        ):
            raise ValueError("redaction secrets must be a tuple of non-empty strings")

    def clean(self, value: str, *, partial: bool = False) -> str:
        for secret in sorted(self.secrets, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
            if partial:
                for length in range(min(len(secret) - 1, len(value)), 0, -1):
                    if value.endswith(secret[:length]):
                        value = value[:-length] + "[REDACTED]"
                        break
        value = re.sub(
            r"(?i)\b(password|token|secret|api[_-]?key|authorization)(\s*[:=]\s*)[^\s]+",
            r"\1\2[REDACTED]",
            value,
        )
        value = re.sub(r"(?i)\bBearer\s+[^\s]+", "Bearer [REDACTED]", value)
        return re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1[REDACTED]@", value)


@dataclass(frozen=True)
class Command:
    """Trusted adapter/engine input only; never decoded from catalog/remote metadata."""

    argv: tuple[str, ...] = field(repr=False)
    description: str
    target_os: OperatingSystem
    source_id: str
    mutates: bool = False
    privilege: PrivilegeRequirement = PrivilegeRequirement.NONE
    timeout_seconds: float = 60.0
    environment: tuple[tuple[str, str], ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.argv, tuple) or not self.argv:
            raise ValueError("command requires an immutable argument array")
        for argument in self.argv:
            text(argument, "command argument")
        text(self.description, "command description")
        text(self.source_id, "source id")
        if (
            not isinstance(self.target_os, OperatingSystem)
            or self.target_os == OperatingSystem.UNKNOWN
        ):
            raise ValueError("commands require a known target operating system")
        if (
            not isinstance(self.privilege, PrivilegeRequirement)
            or type(self.mutates) is not bool
        ):
            raise ValueError("invalid command privilege or mutation flag")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("command timeout must be positive and finite")
        if not isinstance(self.environment, tuple):
            raise ValueError("environment overrides require an immutable tuple")
        keys = []
        for entry in self.environment:
            if not isinstance(entry, tuple) or len(entry) != 2:
                raise ValueError("environment overrides require key/value pairs")
            key, value = entry
            if (
                not isinstance(key, str)
                or re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) is None
            ):
                raise ValueError("invalid environment key")
            if not isinstance(value, str) or "\x00" in value:
                raise ValueError("invalid environment value")
            keys.append(key)
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate environment overrides")

    def preview(self, redactor: Redactor | None = None) -> CommandPreview:
        sanitizer = redactor or Redactor()
        display = []
        hide_next = False
        for argument in self.argv:
            display.append("[REDACTED]" if hide_next else sanitizer.clean(argument))
            hide_next = (
                re.fullmatch(
                    r"(?i)--?(?:token|password|secret|api[-_]?key|authorization)",
                    argument,
                )
                is not None
            )
        return CommandPreview(tuple(display), self.description)

    @property
    def requires_consent(self) -> bool:
        return self.mutates or self.privilege != PrivilegeRequirement.NONE


@dataclass(frozen=True)
class Approval:
    """An application records actual user approval of this exact immutable request."""

    command: Command = field(repr=False)
    elevation: bool = False
    approved_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not isinstance(self.command, Command) or type(self.elevation) is not bool:
            raise ValueError("invalid approval record")
        aware_timestamp(self.approved_at, "approval time")

    def permits(self, command: Command) -> bool:
        return self.command == command and (
            command.privilege == PrivilegeRequirement.NONE or self.elevation
        )


@dataclass(frozen=True)
class OutputSnapshot:
    stream: str
    text: str = field(repr=False)
    truncated: bool = False


@dataclass(frozen=True)
class CommandResult:
    preview: CommandPreview
    started_at: datetime
    finished_at: datetime
    exit_code: int | None = None
    stdout: str = field(default="", repr=False)
    stderr: str = field(default="", repr=False)
    output_truncated: bool = False
    error: OperationError | None = None

    @property
    def succeeded(self) -> bool:
        return self.error is None and self.exit_code == 0
