"""Translate native coordination and launch-identity failures at service boundaries."""

from collections.abc import Iterator
from contextlib import contextmanager
from threading import Event

from models.errors import ErrorCode
from package_managers.base import AdapterError
from system.coordination import CoordinationError, NativeCoordinator
from system.identity import IdentityError


@contextmanager
def access(
    coordinator: NativeCoordinator, cancel: Event | None = None
) -> Iterator[None]:
    try:
        with coordinator.hold(cancel):
            yield
    except CoordinationError as error:
        raise AdapterError(error.error.code, error.error.message) from None
    except IdentityError as error:
        raise AdapterError(ErrorCode.VERIFICATION_FAILED, str(error)) from None
