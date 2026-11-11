"""Small shared validation helpers for domain constructors."""

import re
from enum import Enum
from typing import TypeVar

E = TypeVar("E", bound=Enum)


def text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    if value != value.strip() or any(
        ord(char) < 32 or ord(char) == 127 for char in value
    ):
        raise ValueError(
            f"{field} must not contain outer whitespace or control characters"
        )


def identifier(value: str, field: str) -> None:
    text(value, field)
    if re.fullmatch(r"[a-z0-9]+(?:[._-][a-z0-9]+)*", value) is None:
        raise ValueError(f"{field} must be a lowercase stable identifier")


def enum_value(value: E, enum: type[E], field: str) -> None:
    if not isinstance(value, enum):
        raise ValueError(f"{field} must be a {enum.__name__}")


def unique_tuple(value: tuple[object, ...], field: str) -> None:
    if not isinstance(value, tuple) or any(
        item in value[:index] for index, item in enumerate(value)
    ):
        raise ValueError(f"{field} must be an immutable tuple without duplicates")
