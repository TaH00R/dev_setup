"""Bounded app-event logging with a fixed metadata-only schema."""

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from models._validation import identifier
from models.planning import utc_now

from .store import StateError


class _QuietRotatingHandler(RotatingFileHandler):
    def handleError(self, record: logging.LogRecord) -> None:
        # Disk failures must not produce raw handler tracebacks/paths on stderr.
        pass


class SafeEventLog:
    """No message/output/env argument exists, so callers cannot log raw output."""

    def __init__(self, path: Path) -> None:
        path = Path(path)
        if path.is_symlink() or path.with_name(path.name + ".1").is_symlink():
            raise StateError("Application event logs cannot use symlinks.")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._handler = _QuietRotatingHandler(
                path, maxBytes=65536, backupCount=1, encoding="utf-8"
            )
            self._handler.setFormatter(logging.Formatter("%(message)s"))
        except OSError:
            raise StateError("The application event log could not be opened.") from None
        self._closed = False

    def record(
        self, event: str, *, code: str | None = None, operation_id: str | None = None
    ) -> None:
        for name, value in (
            ("event", event),
            ("code", code),
            ("operation_id", operation_id),
        ):
            if value is not None:
                identifier(value, name)
                if len(value) > 128:
                    raise ValueError("event metadata exceeds its bound")
        if self._closed:
            raise StateError("The event log is closed.")
        payload = {"timestamp": utc_now().isoformat(), "event": event}
        if code is not None:
            payload["code"] = code
        if operation_id is not None:
            payload["operation_id"] = operation_id
        record = logging.LogRecord(
            "dev_setup.events",
            logging.INFO,
            "",
            0,
            json.dumps(payload, sort_keys=True),
            (),
            None,
        )
        self._handler.handle(record)

    def close(self) -> None:
        if not self._closed:
            self._handler.close()
            self._closed = True
