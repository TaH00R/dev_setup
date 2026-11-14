"""Blocking worker-side process runner with bounded pipes and cancellation."""

import os
import platform
import queue
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

from models.diagnostics import DiagnosticCode, DiagnosticComponent, DiagnosticEvent
from models.errors import ErrorCode, OperationError
from models.planning import utc_now

from .commands import Approval, Command, CommandResult, OutputSnapshot, Redactor
from .coordination import CoordinationError, NativeCoordinator
from .diagnostics import record_diagnostic
from .identity import IdentityError, revalidate
from .platform import normalize_os
from .processes import ProcessScope, read_available

OutputObserver = Callable[[OutputSnapshot], None]


class CommandRunner:
    """Call from a worker, never the GUI thread. No log files or passwords."""

    def __init__(
        self,
        *,
        capture_bytes: int = 65536,
        redactor: Redactor | None = None,
        coordinator: NativeCoordinator | None = None,
    ) -> None:
        if type(capture_bytes) is not int or not 1 <= capture_bytes <= 65536:
            raise ValueError(
                "capture limit must be between 1 and 65536 bytes per stream"
            )
        self.capture_bytes = capture_bytes
        self.redactor = redactor or Redactor()
        self.coordinator = coordinator or NativeCoordinator()

    def run(
        self,
        command: Command,
        *,
        approval: Approval | None = None,
        cancel: threading.Event | None = None,
        observe: OutputObserver | None = None,
    ) -> CommandResult:
        try:
            with self.coordinator.hold(cancel):
                return self._run(
                    command, approval=approval, cancel=cancel, observe=observe
                )
        except CoordinationError as error:
            now = utc_now()
            sanitizer = Redactor(
                self.redactor.secrets
                + tuple(value for key, value in command.environment if value)
            )
            return CommandResult(
                command.preview(sanitizer), now, now, error=error.error
            )

    def _run(
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
        diagnostics: list[DiagnosticEvent] = []

        def note(
            code: DiagnosticCode, error_code: ErrorCode = ErrorCode.UNKNOWN
        ) -> None:
            event = record_diagnostic(
                code, DiagnosticComponent.RUNNER, error_code=error_code
            )
            if event is not None and len(diagnostics) < 16:
                diagnostics.append(event)

        def failure(code: ErrorCode, message: str) -> CommandResult:
            return CommandResult(
                preview,
                started,
                utc_now(),
                error=OperationError(code, message),
                diagnostics=tuple(diagnostics),
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
        if command.requires_consent or command.launch_files:
            try:
                revalidate(command, environment=environment)
            except IdentityError as identity_error:
                return failure(ErrorCode.VERIFICATION_FAILED, str(identity_error))
        try:
            scope = ProcessScope()
        except Exception:
            note(DiagnosticCode.PROCESS_CONTROL_FAILED, ErrorCode.PROCESS_CONTROL)
            return failure(
                ErrorCode.PROCESS_CONTROL,
                "Owned process tracking could not be initialized; no command was started.",
            )
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
                creationflags=scope.creationflags,
            )
        except FileNotFoundError:
            scope.close()
            note(DiagnosticCode.PROCESS_START_FAILED, ErrorCode.MISSING_BACKEND)
            return failure(
                ErrorCode.MISSING_BACKEND, "The command executable is not available."
            )
        except PermissionError:
            scope.close()
            note(DiagnosticCode.PROCESS_START_FAILED, ErrorCode.PERMISSION_DENIED)
            return failure(
                ErrorCode.PERMISSION_DENIED,
                "The operating system refused to start the executable.",
            )
        except OSError:
            scope.close()
            note(DiagnosticCode.PROCESS_START_FAILED, ErrorCode.COMMAND_FAILED)
            return failure(
                ErrorCode.COMMAND_FAILED,
                "The operating system could not start the process.",
            )
        except BaseException:
            scope.close()
            raise
        try:
            scope.attach(process)
        except Exception:
            note(DiagnosticCode.PROCESS_CONTROL_FAILED, ErrorCode.PROCESS_CONTROL)
            try:
                try:
                    scope.stop(process)
                except Exception:
                    pass
            finally:
                scope.close()
                if process.poll() is None:
                    try:
                        process.kill()
                        process.wait(timeout=1)
                    except (OSError, subprocess.TimeoutExpired):
                        note(
                            DiagnosticCode.PROCESS_CONTROL_FAILED,
                            ErrorCode.PROCESS_CONTROL,
                        )
                for pipe in (process.stdout, process.stderr):
                    if pipe is not None:
                        pipe.close()
            return failure(
                ErrorCode.PROCESS_CONTROL,
                "Owned process setup could not be completed. Inspect native state before retrying.",
            )

        chunks: queue.Queue[tuple[str, bytes | None]] = queue.Queue(maxsize=32)
        stop_readers = threading.Event()
        pipe_failed = threading.Event()

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
                    chunk = read_available(pipe.fileno())
                    if chunk is None:
                        stop_readers.wait(0.01)
                        continue
                    if not chunk:
                        break
                    deliver((name, chunk))
            except Exception:
                if not stop_readers.is_set():
                    pipe_failed.set()
            finally:
                deliver((name, None))

        assert process.stdout is not None and process.stderr is not None
        readers = [
            threading.Thread(
                target=read_pipe,
                args=(name, pipe),
                daemon=True,
                name=f"dev-setup-pipe-{process.pid}-{name}",
            )
            for name, pipe in (("stdout", process.stdout), ("stderr", process.stderr))
        ]
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        truncated = {"stdout": False, "stderr": False}
        ended = 0
        deadline = time.monotonic() + command.timeout_seconds
        error = None

        def cleanup_failed(code: DiagnosticCode) -> None:
            nonlocal error
            note(code, ErrorCode.PROCESS_CONTROL)
            if error is None:
                error = OperationError(
                    ErrorCode.PROCESS_CONTROL,
                    "Owned process or pipe cleanup could not be confirmed; inspect native state before retrying.",
                    details=(code.value,),
                )

        try:
            for reader in readers:
                reader.start()
            while ended < 2 or process.poll() is None:
                if pipe_failed.is_set():
                    note(DiagnosticCode.PIPE_READ_FAILED, ErrorCode.COMMAND_FAILED)
                    error = OperationError(
                        ErrorCode.COMMAND_FAILED,
                        "Owned process output could not be read completely.",
                        details=(DiagnosticCode.PIPE_READ_FAILED.value,),
                    )
                    break
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
                previously_truncated = truncated[name]
                buffers[name].extend(chunk[:available])
                truncated[name] |= len(chunk) > available
                if observe is not None and (
                    available > 0 or previously_truncated != truncated[name]
                ):
                    # Snapshots replace prior stream text; explicit secrets split
                    # across pipe reads are masked even while incomplete.
                    snapshot = sanitizer.clean(
                        buffers[name].decode("utf-8", errors="ignore"), partial=True
                    )
                    try:
                        observe(
                            OutputSnapshot(
                                name,
                                snapshot[:65536],
                                truncated[name] or len(snapshot) > 65536,
                            )
                        )
                    except Exception:
                        note(DiagnosticCode.OBSERVER_FAILED)
                        observe = None  # A UI/log subscriber cannot change the native outcome.
            if error is None and pipe_failed.is_set():
                note(DiagnosticCode.PIPE_READ_FAILED, ErrorCode.COMMAND_FAILED)
                error = OperationError(
                    ErrorCode.COMMAND_FAILED,
                    "Owned process output could not be read completely.",
                    details=(DiagnosticCode.PIPE_READ_FAILED.value,),
                )
            if error is None:
                process.wait(timeout=1)
        except Exception:
            note(DiagnosticCode.PROCESS_MONITOR_FAILED, ErrorCode.COMMAND_FAILED)
            error = OperationError(
                ErrorCode.COMMAND_FAILED,
                "Owned process monitoring failed; inspect the safe diagnostic code.",
                details=(DiagnosticCode.PROCESS_MONITOR_FAILED.value,),
            )
        finally:
            try:
                stopped = scope.stop(process)
            except Exception:
                stopped = False
            if not stopped:
                cleanup_failed(DiagnosticCode.PROCESS_CONTROL_FAILED)
            stop_readers.set()
            for reader in readers:
                if reader.ident is not None:
                    reader.join(timeout=1)
                if reader.is_alive():
                    cleanup_failed(DiagnosticCode.PIPE_CLEANUP_FAILED)
            for pipe in (process.stdout, process.stderr):
                try:
                    pipe.close()
                except Exception:
                    cleanup_failed(DiagnosticCode.PIPE_CLEANUP_FAILED)
            try:
                scope.close()
            except Exception:
                cleanup_failed(DiagnosticCode.PROCESS_CONTROL_FAILED)
            if process.poll() is None:
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    cleanup_failed(DiagnosticCode.PROCESS_CONTROL_FAILED)
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
            diagnostics=tuple(diagnostics),
        )
