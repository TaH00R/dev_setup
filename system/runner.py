"""Blocking worker-side process runner with bounded pipes and cancellation."""

import os
import platform
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

from models.errors import ErrorCode, OperationError
from models.planning import utc_now

from .commands import Approval, Command, CommandResult, OutputSnapshot, Redactor
from .platform import normalize_os

OutputObserver = Callable[[OutputSnapshot], None]


class CommandRunner:
    """Call from a worker, never the GUI thread. No log files or passwords."""

    def __init__(
        self, *, capture_bytes: int = 65536, redactor: Redactor | None = None
    ) -> None:
        if type(capture_bytes) is not int or not 1 <= capture_bytes <= 65536:
            raise ValueError(
                "capture limit must be between 1 and 65536 bytes per stream"
            )
        self.capture_bytes = capture_bytes
        self.redactor = redactor or Redactor()

    @staticmethod
    def _stop(process: subprocess.Popen[bytes]) -> bool:
        try:
            if sys.platform != "win32":
                os.killpg(process.pid, signal.SIGTERM)
            elif process.poll() is None:
                process.terminate()
        except OSError:
            return process.poll() is not None
        try:
            process.wait(timeout=0.5)
            return True
        except subprocess.TimeoutExpired:
            try:
                if sys.platform != "win32":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
                process.wait(timeout=1)
                return True
            except (OSError, subprocess.TimeoutExpired):
                return process.poll() is not None

    def run(
        self,
        command: Command,
        *,
        approval: Approval | None = None,
        cancel: threading.Event | None = None,
        observe: OutputObserver | None = None,
    ) -> CommandResult:
        started = utc_now()
        # Environment overrides are never displayed; their values are also redacted.
        public_keys = {
            "LC_ALL",
            "HOMEBREW_NO_AUTO_UPDATE",
            "HOMEBREW_NO_INSTALL_CLEANUP",
            "HOMEBREW_NO_ANALYTICS",
        }
        sanitizer = Redactor(
            self.redactor.secrets
            + tuple(
                value
                for key, value in command.environment
                if value and key not in public_keys
            )
        )
        preview = command.preview(sanitizer)

        def failure(code: ErrorCode, message: str) -> CommandResult:
            return CommandResult(
                preview, started, utc_now(), error=OperationError(code, message)
            )

        if command.target_os != normalize_os(platform.system()):
            return failure(
                ErrorCode.UNSUPPORTED_PLATFORM,
                "Command targets a different operating system.",
            )
        if cancel is not None and cancel.is_set():
            return failure(
                ErrorCode.CANCELLED, "Cancelled before starting the process."
            )
        if command.requires_consent and (
            approval is None or not approval.permits(command)
        ):
            return failure(
                ErrorCode.PERMISSION_DENIED,
                "Explicit approval of this command and any elevation is required.",
            )
        if Path(command.argv[0]).suffix.lower() in {".bat", ".cmd", ".ps1"}:
            return failure(
                ErrorCode.PERMISSION_DENIED,
                "Script launchers require an explicit reviewed adapter bridge.",
            )
        environment = os.environ.copy()
        environment.update(command.environment)
        try:
            process = subprocess.Popen(
                list(command.argv),
                shell=False,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                close_fds=True,
                start_new_session=os.name != "nt",
                env=environment,
            )
        except FileNotFoundError:
            return failure(
                ErrorCode.MISSING_BACKEND, "The command executable is not available."
            )
        except PermissionError:
            return failure(
                ErrorCode.PERMISSION_DENIED,
                "The operating system refused to start the executable.",
            )
        except OSError:
            return failure(
                ErrorCode.COMMAND_FAILED,
                "The operating system could not start the process.",
            )

        chunks: queue.Queue[tuple[str, bytes | None]] = queue.Queue(maxsize=32)
        stop_readers = threading.Event()

        def deliver(item: tuple[str, bytes | None]) -> None:
            while not stop_readers.is_set():
                try:
                    chunks.put(item, timeout=0.05)
                    return
                except queue.Full:
                    continue

        def read_pipe(name: str, pipe: BinaryIO) -> None:
            try:
                while not stop_readers.is_set():
                    chunk = os.read(pipe.fileno(), 4096)
                    if not chunk:
                        break
                    deliver((name, chunk))
            except OSError:
                pass
            finally:
                deliver((name, None))

        assert process.stdout is not None and process.stderr is not None
        readers = [
            threading.Thread(target=read_pipe, args=(name, pipe), daemon=True)
            for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr))
        ]
        for reader in readers:
            reader.start()
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        truncated = {"stdout": False, "stderr": False}
        ended = 0
        deadline = time.monotonic() + command.timeout_seconds
        error = None
        try:
            while ended < 2 or process.poll() is None:
                if cancel is not None and cancel.is_set():
                    error = OperationError(
                        ErrorCode.CANCELLED,
                        "Process cancellation requested; system changes may be partial.",
                    )
                    break
                if time.monotonic() >= deadline:
                    error = OperationError(
                        ErrorCode.TIMEOUT,
                        "Process exceeded its timeout; system changes may be partial.",
                    )
                    break
                try:
                    name, chunk = chunks.get(timeout=0.02)
                except queue.Empty:
                    continue
                if chunk is None:
                    ended += 1
                    continue
                available = self.capture_bytes - len(buffers[name])
                buffers[name].extend(chunk[:available])
                truncated[name] |= len(chunk) > available
                if observe is not None:
                    # Snapshots replace prior stream text; explicit secrets split
                    # across pipe reads are masked even while incomplete.
                    snapshot = sanitizer.clean(
                        buffers[name].decode("utf-8", errors="ignore"), partial=True
                    )
                    observe(
                        OutputSnapshot(
                            name,
                            snapshot[:65536],
                            truncated[name] or len(snapshot) > 65536,
                        )
                    )
            if error is not None:
                self._stop(process)
            else:
                process.wait(timeout=1)
        except Exception:
            self._stop(process)
            error = OperationError(
                ErrorCode.COMMAND_FAILED,
                "Output observation or process monitoring failed.",
            )
        finally:
            if process.poll() is None:
                self._stop(process)
            stop_readers.set()
            for pipe in (process.stdout, process.stderr):
                pipe.close()
            for reader in readers:
                reader.join(timeout=0.2)
        if error is None and process.returncode != 0:
            error = OperationError(
                ErrorCode.COMMAND_FAILED, "The command exited with a non-zero status."
            )

        def captured(name: str) -> str:
            clean = sanitizer.clean(
                buffers[name].decode("utf-8", errors="replace"), partial=True
            )
            if len(clean) > 65536:
                truncated[name] = True
            return clean[:65536]

        stdout, stderr = captured("stdout"), captured("stderr")
        return CommandResult(
            preview,
            started,
            utc_now(),
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
            output_truncated=any(truncated.values()),
            error=error,
        )
