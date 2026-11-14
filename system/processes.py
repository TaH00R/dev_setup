"""Owned process groups/jobs and nonblocking pipe polling, behind OS boundaries."""

import ctypes
import os
import select
import signal
import subprocess
import sys
import time
from ctypes import wintypes
from functools import lru_cache
from typing import Any


class ProcessControlError(ValueError):
    pass


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            "read_ops",
            "write_ops",
            "other_ops",
            "read_bytes",
            "write_bytes",
            "other_bytes",
        )
    ]


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_longlong),
        ("job_time", ctypes.c_longlong),
        ("flags", wintypes.DWORD),
        ("min_working_set", ctypes.c_size_t),
        ("max_working_set", ctypes.c_size_t),
        ("active_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority", wintypes.DWORD),
        ("scheduling", wintypes.DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimits),
        ("io", _IoCounters),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("user_time", ctypes.c_longlong),
        ("kernel_time", ctypes.c_longlong),
        ("period_user", ctypes.c_longlong),
        ("period_kernel", ctypes.c_longlong),
        ("page_faults", wintypes.DWORD),
        ("total", wintypes.DWORD),
        ("active", wintypes.DWORD),
        ("terminated", wintypes.DWORD),
    ]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("usage", wintypes.DWORD),
        ("thread_id", wintypes.DWORD),
        ("owner_pid", wintypes.DWORD),
        ("base_priority", wintypes.LONG),
        ("delta_priority", wintypes.LONG),
        ("flags", wintypes.DWORD),
    ]


@lru_cache(maxsize=1)
def _kernel() -> Any:
    api = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    declarations = {
        "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        "SetInformationJobObject": (
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
            wintypes.BOOL,
        ),
        "QueryInformationJobObject": (
            [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.c_void_p,
            ],
            wintypes.BOOL,
        ),
        "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        "OpenProcess": (
            [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD],
            wintypes.HANDLE,
        ),
        "OpenThread": (
            [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD],
            wintypes.HANDLE,
        ),
        "ResumeThread": ([wintypes.HANDLE], wintypes.DWORD),
        "CreateToolhelp32Snapshot": ([wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE),
        "Thread32First": (
            [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)],
            wintypes.BOOL,
        ),
        "Thread32Next": (
            [wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)],
            wintypes.BOOL,
        ),
        "PeekNamedPipe": (
            [
                wintypes.HANDLE,
                ctypes.c_void_p,
                wintypes.DWORD,
                ctypes.c_void_p,
                ctypes.POINTER(wintypes.DWORD),
                ctypes.c_void_p,
            ],
            wintypes.BOOL,
        ),
        "GetLastError": ([], wintypes.DWORD),
    }
    for name, (arguments, result) in declarations.items():
        function = getattr(api, name)
        function.argtypes, function.restype = arguments, result
    return api


class ProcessScope:
    """Windows children cannot run before job assignment; no uncontained fallback."""

    def __init__(self) -> None:
        self.job: int | None = None
        self.assigned = False
        if sys.platform == "win32":
            api = _kernel()
            job = api.CreateJobObjectW(None, None)
            if not job:
                raise ProcessControlError(
                    "Owned process tracking could not be initialized."
                )
            self.job = int(job)
            limits = _ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not api.SetInformationJobObject(
                job, 9, ctypes.byref(limits), ctypes.sizeof(limits)
            ):
                self.close()
                raise ProcessControlError(
                    "Owned process limits could not be initialized."
                )

    @property
    def creationflags(self) -> int:
        return 0x4 if sys.platform == "win32" else 0  # CREATE_SUSPENDED

    def attach(self, process: subprocess.Popen[bytes]) -> None:
        if self.job is None:
            return
        api = _kernel()
        handle = api.OpenProcess(0x0101, False, process.pid)  # SET_QUOTA | TERMINATE
        if not handle:
            raise ProcessControlError(
                "The suspended process could not be owned safely."
            )
        try:
            if not api.AssignProcessToJobObject(self.job, handle):
                raise ProcessControlError(
                    "The suspended process could not be assigned to its owned job."
                )
            self.assigned = True
        finally:
            api.CloseHandle(handle)
        snapshot = api.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
        if snapshot in (None, ctypes.c_void_p(-1).value):
            raise ProcessControlError(
                "The suspended process thread could not be inspected."
            )
        try:
            entry = _ThreadEntry()
            entry.size = ctypes.sizeof(entry)
            available = api.Thread32First(snapshot, ctypes.byref(entry))
            while available:
                if entry.owner_pid == process.pid:
                    thread = api.OpenThread(
                        0x2, False, entry.thread_id
                    )  # SUSPEND_RESUME
                    if not thread:
                        raise ProcessControlError(
                            "The owned process thread could not be resumed."
                        )
                    try:
                        if api.ResumeThread(thread) != 1:
                            raise ProcessControlError(
                                "The owned process was not in its expected suspended state."
                            )
                    finally:
                        api.CloseHandle(thread)
                    return
                available = api.Thread32Next(snapshot, ctypes.byref(entry))
        finally:
            api.CloseHandle(snapshot)
        raise ProcessControlError("No owned suspended thread was available.")

    def stop(self, process: subprocess.Popen[bytes]) -> bool:
        try:
            if self.job is not None:
                if self.assigned:
                    if not _kernel().TerminateJobObject(self.job, 1):
                        return False
                elif process.poll() is None:
                    process.kill()  # Never resumed: no child could have been spawned.
                process.wait(timeout=1)
                deadline = time.monotonic() + 1
                while self.assigned:
                    accounting = _Accounting()
                    if not _kernel().QueryInformationJobObject(
                        self.job,
                        1,
                        ctypes.byref(accounting),
                        ctypes.sizeof(accounting),
                        None,
                    ):
                        return False
                    if accounting.active == 0:
                        break
                    if time.monotonic() >= deadline:
                        return False
                    time.sleep(0.01)
                return True
            if sys.platform == "win32":
                return False
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            else:
                time.sleep(0.1)
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=1)
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False

    def close(self) -> None:
        if self.job is not None:
            _kernel().CloseHandle(self.job)
            self.job = None


def read_available(descriptor: int) -> bytes | None:
    """None means no bytes yet; empty bytes means EOF. Never wait for pipe data."""
    if sys.platform == "win32":
        import msvcrt

        available = wintypes.DWORD()
        api = _kernel()
        if not api.PeekNamedPipe(
            msvcrt.get_osfhandle(descriptor),
            None,
            0,
            None,
            ctypes.byref(available),
            None,
        ):
            if api.GetLastError() in {109, 233}:  # BROKEN_PIPE / PIPE_NOT_CONNECTED
                return b""
            raise OSError("Owned pipe polling failed.")
        return (
            os.read(descriptor, min(4096, available.value)) if available.value else None
        )
    ready, _, _ = select.select([descriptor], [], [], 0)
    return os.read(descriptor, 4096) if ready else None
