"""Curated-first parallel discovery with bounded cache and cooperative cancellation."""

import math
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from threading import Event, Lock
from typing import Protocol

from models.discovery import LivePackage, ProviderBatch, ProviderStatus, SearchState
from models.errors import ErrorCode, OperationError
from models.platform import Platform
from package_managers.base import AdapterError

from .catalog.service import CatalogMatch, CatalogService


class SearchProvider(Protocol):
    """Implementations must honor cancellation and their native command timeout."""

    id: str
    backend: str
    host: Platform

    def search(
        self, query: str, cancel: Event, timeout_seconds: float
    ) -> ProviderBatch: ...


@dataclass(frozen=True)
class SearchSnapshot:
    query: str
    curated: tuple[CatalogMatch, ...]
    live: tuple[LivePackage, ...]
    providers: tuple[ProviderStatus, ...]
    cancelled: bool = False
    note: str = ""

    @property
    def complete(self) -> bool:
        return all(p.state != SearchState.SEARCHING for p in self.providers)


@dataclass(frozen=True)
class CachedBatch:
    batch: ProviderBatch
    created: float


SearchObserver = Callable[[SearchSnapshot], None]


class DiscoveryService:
    def __init__(
        self,
        catalog: CatalogService,
        host: Platform,
        providers: tuple[SearchProvider, ...] = (),
        *,
        timeout_seconds: float = 5.0,
        cache_ttl_seconds: float = 60.0,
        cache_entries: int = 64,
        result_limit: int = 200,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if any(
            not math.isfinite(v) or v <= 0 for v in (timeout_seconds, cache_ttl_seconds)
        ):
            raise ValueError(
                "search timeout and cache lifetime must be positive and finite"
            )
        if (
            type(cache_entries) is not int
            or not 1 <= cache_entries <= 256
            or type(result_limit) is not int
            or not 1 <= result_limit <= 200
        ):
            raise ValueError("invalid discovery cache/result bounds")
        if len({p.id for p in providers}) != len(providers) or any(
            p.host != host for p in providers
        ):
            raise ValueError("providers must be unique and match the search platform")
        self.catalog, self.host, self.providers = catalog, host, providers
        self.timeout_seconds, self.cache_ttl = timeout_seconds, cache_ttl_seconds
        self.cache_entries, self.result_limit, self.clock = (
            cache_entries,
            result_limit,
            clock,
        )
        self._cache: OrderedDict[tuple[str, str], CachedBatch] = OrderedDict()
        self._cache_lock = Lock()

    def initial(
        self,
        query: str,
        *,
        category: str | None = None,
        mapped_only: bool = False,
        enabled: bool = True,
    ) -> SearchSnapshot:
        query = " ".join(query.split())
        curated = self.catalog.search(
            query, category=category, target=self.host, mapped_only=mapped_only
        )
        note = ""
        if not enabled:
            note = "Live search is off."
        elif category is not None:
            note = "Category filters apply to curated tools. Clear the category for live discovery."
        elif len(query) < 2:
            note = "Type at least 2 characters to search live sources."
        elif not self.providers:
            note = "No live providers are configured for this platform."
        statuses = (
            ()
            if note
            else tuple(
                ProviderStatus(p.id, SearchState.SEARCHING) for p in self.providers
            )
        )
        return SearchSnapshot(query, curated, (), statuses, note=note)

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._cache.clear()

    def _cached(self, key: tuple[str, str]) -> CachedBatch | None:
        with self._cache_lock:
            item = self._cache.get(key)
            if item and 0 <= self.clock() - item.created < self.cache_ttl:
                self._cache.move_to_end(key)
                return item
            self._cache.pop(key, None)
            return None

    def _remember(self, key: tuple[str, str], batch: ProviderBatch) -> None:
        with self._cache_lock:
            self._cache[key] = CachedBatch(batch, self.clock())
            self._cache.move_to_end(key)
            while len(self._cache) > self.cache_entries:
                self._cache.popitem(last=False)

    def search(
        self,
        query: str,
        cancel: Event,
        *,
        category: str | None = None,
        mapped_only: bool = False,
        enabled: bool = True,
        force: bool = False,
        observe: SearchObserver | None = None,
    ) -> SearchSnapshot:
        initial = self.initial(
            query, category=category, mapped_only=mapped_only, enabled=enabled
        )
        statuses = {p.provider_id: p for p in initial.providers}
        batches: dict[str, ProviderBatch] = {}
        query = initial.query

        def snapshot(cancelled: bool = False) -> SearchSnapshot:
            seen = {
                (spec.backend, spec.source.id, spec.native_id)
                for match in initial.curated
                for spec in match.matching_specs
            }
            packages = []
            for provider in self.providers:
                for package in batches.get(provider.id, ProviderBatch(())).packages:
                    if package.key not in seen:
                        seen.add(package.key)
                        packages.append(package)
            packages.sort(
                key=lambda p: (
                    0
                    if query.casefold() in (p.name.casefold(), p.native_id.casefold())
                    else 1,
                    p.name.casefold(),
                    p.backend,
                    p.source_id,
                    p.native_id,
                )
            )
            return SearchSnapshot(
                query,
                initial.curated,
                tuple(packages[: self.result_limit]),
                tuple(statuses[p.id] for p in self.providers if p.id in statuses),
                cancelled,
                f"Live results are limited to {self.result_limit}. Refine the query."
                if len(packages) > self.result_limit
                else initial.note,
            )

        def publish(value: SearchSnapshot) -> None:
            if observe:
                try:
                    observe(value)
                except Exception:
                    pass

        publish(initial)
        if initial.complete:
            return initial
        deadline = time.monotonic() + self.timeout_seconds
        futures: dict[Future[ProviderBatch], SearchProvider] = {}
        executor = ThreadPoolExecutor(
            max_workers=min(4, len(self.providers)), thread_name_prefix="package-search"
        )
        timed_out = False
        try:
            for provider in self.providers:
                cached = None if force else self._cached((provider.id, query))
                if cached is not None:
                    batches[provider.id] = cached.batch
                    statuses[provider.id] = ProviderStatus(
                        provider.id,
                        SearchState.CACHED,
                        len(cached.batch.packages),
                        limited=cached.batch.limited,
                        cache_age_seconds=max(0, self.clock() - cached.created),
                    )
                elif not cancel.is_set():
                    futures[
                        executor.submit(
                            provider.search, query, cancel, self.timeout_seconds
                        )
                    ] = provider
            publish(snapshot())
            while futures and not cancel.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    cancel.set()
                    break
                done, _ = wait(
                    futures, timeout=min(0.03, remaining), return_when=FIRST_COMPLETED
                )
                for future in done:
                    provider = futures.pop(future)
                    try:
                        batch = future.result()
                        if any(
                            p.backend != provider.backend or p.platform != self.host
                            for p in batch.packages
                        ):
                            raise AdapterError(
                                ErrorCode.VERIFICATION_FAILED,
                                "Provider returned a mismatched source/backend platform identity.",
                            )
                        batch = ProviderBatch(
                            batch.packages[: self.result_limit],
                            batch.limited or len(batch.packages) > self.result_limit,
                        )
                        if not cancel.is_set():
                            batches[provider.id] = batch
                            statuses[provider.id] = ProviderStatus(
                                provider.id,
                                SearchState.SUCCEEDED,
                                len(batch.packages),
                                limited=batch.limited,
                            )
                            self._remember((provider.id, query), batch)
                    except AdapterError as error:
                        state = {
                            ErrorCode.MISSING_BACKEND: SearchState.UNAVAILABLE,
                            ErrorCode.CANCELLED: SearchState.CANCELLED,
                            ErrorCode.TIMEOUT: SearchState.TIMEOUT,
                        }.get(error.error.code, SearchState.FAILED)
                        statuses[provider.id] = ProviderStatus(
                            provider.id, state, error=error.error
                        )
                    except Exception:
                        statuses[provider.id] = ProviderStatus(
                            provider.id,
                            SearchState.FAILED,
                            error=OperationError(
                                ErrorCode.UNKNOWN,
                                "Native search failed unexpectedly; untrusted exception text was omitted.",
                            ),
                        )
                    publish(snapshot(cancel.is_set() and not timed_out))
            for provider in self.providers:
                if statuses[provider.id].state == SearchState.SEARCHING:
                    code = ErrorCode.TIMEOUT if timed_out else ErrorCode.CANCELLED
                    statuses[provider.id] = ProviderStatus(
                        provider.id,
                        SearchState.TIMEOUT if timed_out else SearchState.CANCELLED,
                        error=OperationError(
                            code,
                            "Search exceeded its time budget."
                            if timed_out
                            else "Search was cancelled.",
                        ),
                    )
            result = snapshot(cancel.is_set() and not timed_out)
            publish(result)
            return result
        finally:
            for future in futures:
                future.cancel()
            # Native providers honor the event/runner timeout. Wait for cleanup so
            # closing a GUI cannot leave an unowned search thread running.
            executor.shutdown(wait=True, cancel_futures=True)
