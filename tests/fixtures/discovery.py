"""Deterministic providers that never invoke package managers or the network."""

import threading
import time

from models.discovery import LivePackage, ProviderBatch
from models.errors import ErrorCode
from package_managers.base import AdapterError


class FakeProvider:
    def __init__(self, host, provider_id="demo", backend=None):
        self.host, self.id = host, provider_id
        self.backend = backend or provider_id
        self.calls = []
        self.thread_ids = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.block_queries = set()
        self.error = None
        self.rows = {}
        self.callback = None

    def hit(
        self,
        native_id="demo-tool",
        *,
        source="repository",
        name="Demo tool",
        summary="Synthetic live metadata",
    ):
        return LivePackage(self.backend, source, native_id, name, summary, self.host)

    def search(self, query, cancel, timeout_seconds):
        self.calls.append(query)
        self.thread_ids.append(threading.get_ident())
        self.started.set()
        deadline = time.monotonic() + timeout_seconds
        while query in self.block_queries and not self.release.wait(0.005):
            if cancel.is_set():
                raise AdapterError(ErrorCode.CANCELLED, "Synthetic search cancelled.")
            if time.monotonic() >= deadline:
                raise AdapterError(ErrorCode.TIMEOUT, "Synthetic search timed out.")
        if self.callback:
            return self.callback(query, cancel, timeout_seconds)
        if self.error:
            raise AdapterError(self.error, "Synthetic provider failed.")
        return ProviderBatch(tuple(self.rows.get(query, (self.hit(),))))
