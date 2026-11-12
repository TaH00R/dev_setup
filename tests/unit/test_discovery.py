"""Hybrid ranking, source identity, bounded cache, and partial-result contracts."""

import threading
import unittest
from dataclasses import replace

from models.discovery import ProviderBatch, SearchState
from models.errors import ErrorCode
from models.package import SourceKind, TrustLevel
from models.platform import Architecture, OperatingSystem, Platform
from services.catalog.loader import load_catalog
from services.catalog.service import CatalogService
from services.discovery import DiscoveryService
from tests.fixtures.discovery import FakeProvider


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.host = Platform(OperatingSystem.MACOS, Architecture.ARM64)
        self.catalog = CatalogService(load_catalog())
        self.provider = FakeProvider(self.host)

    def service(self, providers=None, **kwargs):
        return DiscoveryService(
            self.catalog, self.host, providers or (self.provider,), **kwargs
        )

    def test_curated_initial_results_arrive_before_native_work(self):
        service = self.service()
        initial = service.initial("git")
        self.assertEqual(initial.curated[0].package.id, "git")
        self.assertFalse(initial.complete)
        self.assertFalse(self.provider.calls)
        events = []
        final = service.search("git", threading.Event(), observe=events.append)
        self.assertEqual(events[0].curated[0].package.id, "git")
        self.assertFalse(events[0].live)
        self.assertTrue(final.complete)
        self.assertTrue(final.live)

    def test_deduplication_uses_source_and_native_id_not_display_name(self):
        provider = FakeProvider(self.host, "brew-results", "homebrew")
        provider.rows["git"] = (
            provider.hit("git", source="homebrew-core", name="Git"),
            provider.hit("git", source="homebrew-cask", name="Git"),
            provider.hit("git-other", source="homebrew-core", name="Git"),
            provider.hit("git-other", source="homebrew-core", name="Duplicate row"),
        )
        result = self.service((provider,)).search("git", threading.Event())
        self.assertEqual(len(result.live), 2)
        self.assertEqual(
            {p.key for p in result.live},
            {
                ("homebrew", "homebrew-cask", "git"),
                ("homebrew", "homebrew-core", "git-other"),
            },
        )

    def test_live_records_never_gain_curated_trust_or_installability(self):
        hit = self.provider.hit()
        self.assertEqual(hit.kind, SourceKind.LIVE)
        self.assertEqual(hit.trust, TrustLevel.UNKNOWN)
        self.assertFalse(hit.installable)
        with self.assertRaises(ValueError):
            replace(hit, native_id="unsafe\nidentifier")

    def test_providers_run_in_parallel_off_the_calling_thread(self):
        barrier = threading.Barrier(2, timeout=1)
        other = FakeProvider(self.host, "other")

        def search(query, cancel, timeout):
            barrier.wait()
            return ProviderBatch(())

        self.provider.callback = other.callback = search
        result = self.service((self.provider, other)).search("git", threading.Event())
        self.assertTrue(all(p.state == SearchState.SUCCEEDED for p in result.providers))
        self.assertTrue(
            all(
                t != threading.get_ident()
                for p in (self.provider, other)
                for t in p.thread_ids
            )
        )

    def test_provider_failure_retains_curated_and_other_live_results(self):
        other = FakeProvider(self.host, "offline")
        other.error = ErrorCode.NETWORK
        result = self.service((self.provider, other)).search("git", threading.Event())
        self.assertTrue(result.curated)
        self.assertTrue(result.live)
        self.assertEqual(result.providers[1].error.code, ErrorCode.NETWORK)
        self.assertTrue(result.complete)

    def test_missing_manager_is_unavailable_not_an_empty_success(self):
        self.provider.error = ErrorCode.MISSING_BACKEND
        result = self.service().search("git", threading.Event())
        self.assertEqual(result.providers[0].state, SearchState.UNAVAILABLE)
        self.assertFalse(result.live)

    def test_timeout_retains_fast_partial_results(self):
        other = FakeProvider(self.host, "slow")
        other.block_queries.add("git")
        result = self.service((self.provider, other), timeout_seconds=0.08).search(
            "git", threading.Event()
        )
        self.assertTrue(result.live)
        self.assertEqual(result.providers[1].state, SearchState.TIMEOUT)
        self.assertFalse(result.cancelled)

    def test_cancel_before_search_starts_no_provider(self):
        cancel = threading.Event()
        cancel.set()
        result = self.service().search("git", cancel)
        self.assertTrue(result.cancelled)
        self.assertFalse(self.provider.calls)
        self.assertEqual(result.providers[0].state, SearchState.CANCELLED)

    def test_cancel_during_partial_search_preserves_finished_source(self):
        other = FakeProvider(self.host, "slow")
        other.block_queries.add("git")
        cancel = threading.Event()

        def observe(snapshot):
            if snapshot.live:
                cancel.set()

        result = self.service((self.provider, other)).search(
            "git", cancel, observe=observe
        )
        self.assertTrue(result.cancelled)
        self.assertTrue(result.live)
        self.assertEqual(result.providers[1].state, SearchState.CANCELLED)

    def test_success_cache_expires_and_explicit_refresh_bypasses_it(self):
        clock = [10.0]
        service = self.service(clock=lambda: clock[0])
        service.search("git", threading.Event())
        clock[0] += 3
        cached = service.search("git", threading.Event())
        self.assertEqual(cached.providers[0].state, SearchState.CACHED)
        self.assertEqual(cached.providers[0].cache_age_seconds, 3)
        self.assertEqual(len(self.provider.calls), 1)
        service.search("git", threading.Event(), force=True)
        self.assertEqual(len(self.provider.calls), 2)
        clock[0] += 61
        service.search("git", threading.Event())
        self.assertEqual(len(self.provider.calls), 3)

    def test_cache_is_bounded_and_preserves_case_sensitive_queries(self):
        service = self.service(cache_entries=1)
        for query in ("git", "Git", "git"):
            service.search(query, threading.Event())
        self.assertEqual(self.provider.calls, ["git", "Git", "git"])
        service.clear_cache()
        service.search("git", threading.Event())
        self.assertEqual(len(self.provider.calls), 4)

    def test_errors_are_not_cached_but_empty_successes_are(self):
        service = self.service()
        self.provider.error = ErrorCode.NETWORK
        for _ in range(2):
            service.search("git", threading.Event())
        self.assertEqual(len(self.provider.calls), 2)
        self.provider.error = None
        self.provider.rows["git"] = ()
        service.search("git", threading.Event())
        cached = service.search("git", threading.Event())
        self.assertEqual(cached.providers[0].state, SearchState.CACHED)
        self.assertFalse(cached.live)

    def test_short_queries_disabled_live_and_category_filters_do_not_spawn_work(self):
        service = self.service()
        for query, kwargs in (
            ("g", {}),
            ("git", {"enabled": False}),
            ("git", {"category": "api-network"}),
        ):
            result = service.search(query, threading.Event(), **kwargs)
            self.assertTrue(result.complete)
            self.assertTrue(result.note)
        self.assertFalse(self.provider.calls)

    def test_result_limits_are_visible(self):
        other = FakeProvider(self.host, "other")
        for provider in (self.provider, other):
            provider.rows["tool"] = tuple(provider.hit(f"tool-{i}") for i in range(2))
        result = self.service((self.provider, other), result_limit=2).search(
            "tool", threading.Event()
        )
        self.assertEqual(len(result.live), 2)
        self.assertIn("limited", result.note)

    def test_mismatched_provider_identity_is_discarded(self):
        self.provider.rows["git"] = (replace(self.provider.hit(), backend="different"),)
        result = self.service().search("git", threading.Event())
        self.assertFalse(result.live)
        self.assertEqual(result.providers[0].error.code, ErrorCode.VERIFICATION_FAILED)

    def test_unexpected_errors_and_observer_exceptions_are_contained(self):
        def broken(*args):
            raise RuntimeError("password=do-not-expose")

        self.provider.callback = broken
        result = self.service().search("git", threading.Event(), observe=broken)
        self.assertEqual(result.providers[0].state, SearchState.FAILED)
        self.assertNotIn("do-not-expose", repr(result))

    def test_invalid_configuration_and_provider_platform_are_rejected(self):
        for kwargs in (
            {"timeout_seconds": 0},
            {"cache_ttl_seconds": float("nan")},
            {"result_limit": 201},
            {"cache_entries": 0},
        ):
            with self.assertRaises(ValueError):
                self.service(**kwargs)
        with self.assertRaises(ValueError):
            self.service((self.provider, self.provider))
        self.provider.host = Platform(OperatingSystem.WINDOWS, Architecture.X86_64)
        with self.assertRaises(ValueError):
            self.service()
