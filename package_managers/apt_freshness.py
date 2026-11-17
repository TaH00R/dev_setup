"""Time bounds from authenticated releases; never a substitute for signatures."""

import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from models.errors import ErrorCode

from .base import AdapterError


def _fail() -> AdapterError:
    return AdapterError(
        ErrorCode.VERIFICATION_FAILED,
        "APT signed metadata is expired, future-dated or has unsupported time fields. Refresh indexes and check the system clock.",
    )


def _timestamp(value: str) -> datetime:
    if (
        re.fullmatch(
            r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), {1,2}[0-9]{1,2} "
            r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
            r"[0-9]{4} {1,2}[0-9]{1,2}:[0-9]{2}:[0-9]{2} (?:UTC|GMT|\+0000)",
            value,
        )
        is None
    ):
        raise ValueError
    result = parsedate_to_datetime(value)
    if (
        result.utcoffset() != timedelta(0)
        or value[:3]
        != ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[result.weekday()]
    ):
        raise ValueError
    return result.astimezone(timezone.utc)


def release_times(content: bytes) -> tuple[datetime, datetime | None]:
    """Read only the signed body, after the caller has authenticated its bytes."""
    try:
        if len(content) > 2 * 1024 * 1024:
            raise ValueError
        _, separator, body = content.partition(b"\n\n")
        body, signature, _ = body.partition(b"\n-----BEGIN PGP SIGNATURE-----")
        if not separator or not signature:
            raise ValueError
        fields: dict[str, datetime] = {}
        time_field = False
        for line in body.decode("utf-8").splitlines():
            if time_field and line.startswith((" ", "\t")):
                raise ValueError
            name, colon, value = line.partition(":")
            time_field = name.strip().lower() in {"date", "valid-until"}
            if time_field:
                name = name.lower()
                if not colon or name in fields or name != name.strip():
                    raise ValueError
                fields[name] = _timestamp(value.strip())
        created = fields["date"]
        expires = fields.get("valid-until")
        if expires is not None and expires < created:
            raise ValueError
        return created, expires
    except (KeyError, ValueError, OverflowError, UnicodeError):
        raise _fail() from None


def validate_freshness(content: bytes, now: datetime) -> None:
    created, expires = release_times(content)
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise _fail()
    # Matches APT's default ten-second future allowance. No expiry is invented
    # for static base archives which omit Valid-Until.
    if created - now > timedelta(seconds=10) or (expires is not None and now > expires):
        raise _fail()
