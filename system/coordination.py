"""Per-user native access serialization across threads, services and processes."""

import errno
import hashlib
import math
import os
import stat
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from models.errors import ErrorCode, OperationError


class CoordinationError(ValueError):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.error = OperationError(code, message)


class _Shared:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.local = threading.local()
        self.pid = os.getpid()


_registry: dict[str, _Shared] = {}
_registry_lock = threading.Lock()


def _after_fork() -> None:
    global _registry_lock
    _registry.clear()
    _registry_lock = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


class NativeCoordinator:
    """Conservative exclusive reads/writes, reentrant only on the owning thread.

    Kernel locks disappear after a crash; the persistent lock file is never
    deleted, because unlinking it could create two independently locked inodes.
    """

    def __init__(self, root: Path | None = None, *, timeout: float = 10.0) -> None:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout < 0
            or timeout > 60
        ):
            raise ValueError("Native lock timeout must be between zero and 60 seconds.")
        user = (
            str(os.getuid())
            if hasattr(os, "getuid")
            else hashlib.sha256(str(Path.home()).encode()).hexdigest()[:16]
        )
        self.root = Path(
            os.path.abspath(
                root
                or Path(tempfile.gettempdir()).resolve()
                / f"dev-setup-native-locks-{user}"
            )
        )
        self.timeout = timeout
        key = os.path.normcase(str(self.root))
        with _registry_lock:
            self.shared = _registry.setdefault(key, _Shared())

    @staticmethod
    def _safe(path: Path) -> None:
        for item in (path, *path.parents):
            try:
                metadata = item.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(metadata.st_mode) or getattr(
                metadata, "st_file_attributes", 0
            ) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                raise CoordinationError(
                    ErrorCode.PERMISSION_DENIED,
                    "Native coordination paths must not contain links or reparse points.",
                )

    def _open(self) -> int:
        self._safe(self.root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt" and (
            self.root.stat().st_uid != getattr(os, "getuid")()
            or self.root.stat().st_mode & 0o077
        ):
            raise CoordinationError(
                ErrorCode.PERMISSION_DENIED,
                "Native coordination requires a private user-owned directory.",
            )
        path = self.root / "native.lock"
        self._safe(path)
        descriptor = os.open(
            path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
                raise CoordinationError(
                    ErrorCode.PERMISSION_DENIED,
                    "Native coordination requires a private regular lock file.",
                )
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    @staticmethod
    def _kernel_lock(descriptor: int, *, release: bool = False) -> None:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(
                descriptor, msvcrt.LK_UNLCK if release else msvcrt.LK_NBLCK, 1
            )
        else:
            import fcntl

            fcntl.flock(
                descriptor, fcntl.LOCK_UN if release else fcntl.LOCK_EX | fcntl.LOCK_NB
            )

    @contextmanager
    def hold(self, cancel: threading.Event | None = None) -> Iterator[None]:
        owner_pid = os.getpid()
        if self.shared.pid != os.getpid():
            with _registry_lock:
                self.shared = _registry.setdefault(
                    os.path.normcase(str(self.root)), _Shared()
                )
        deadline = time.monotonic() + self.timeout
        acquired = False
        descriptor: int | None = None
        locked = False
        entered = False

        def check() -> None:
            if cancel is not None and cancel.is_set():
                raise CoordinationError(
                    ErrorCode.CANCELLED,
                    "Cancelled while waiting for native access; no command was started.",
                )
            if time.monotonic() > deadline:
                raise CoordinationError(
                    ErrorCode.BUSY,
                    "Another Dev Setup task holds native access. Wait for it to finish; no command was started.",
                )

        try:
            while not acquired:
                if cancel is not None and cancel.is_set():
                    check()
                acquired = self.shared.lock.acquire(blocking=False)
                if not acquired:
                    check()
                    time.sleep(0.02)
            depth = getattr(self.shared.local, "depth", 0)
            if depth:
                entered = True
                yield
                return
            descriptor = self._open()
            while not locked:
                if cancel is not None and cancel.is_set():
                    check()
                try:
                    self._kernel_lock(descriptor)
                    locked = True
                except OSError as error:
                    if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise
                    check()
                    time.sleep(0.02)
            self._safe(self.root / "native.lock")
            disk = (self.root / "native.lock").stat()
            opened = os.fstat(descriptor)
            if (disk.st_dev, disk.st_ino) != (opened.st_dev, opened.st_ino):
                raise CoordinationError(
                    ErrorCode.PERMISSION_DENIED,
                    "Native coordination file changed during acquisition.",
                )
            self.shared.local.depth = 1
            entered = True
            try:
                yield
            finally:
                self.shared.local.depth = 0
        except OSError:
            if entered:
                raise
            raise CoordinationError(
                ErrorCode.PERMISSION_DENIED,
                "Native coordination could not be opened safely; no native access was granted.",
            ) from None
        finally:
            if descriptor is not None:
                try:
                    if locked and owner_pid == os.getpid():
                        try:
                            self._kernel_lock(descriptor, release=True)
                        except OSError:
                            pass  # Closing the descriptor releases its kernel lock.
                finally:
                    os.close(descriptor)
            if acquired and owner_pid == os.getpid():
                self.shared.lock.release()
