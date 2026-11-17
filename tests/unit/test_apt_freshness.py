"""Deterministic signed-time policy contracts; no clock or native system changes."""

import unittest
from datetime import datetime, timedelta, timezone

from models.errors import ErrorCode
from models.planning import OperationAction
from package_managers.apt_cache import cache_fingerprint
from package_managers.apt_freshness import release_times, validate_freshness
from package_managers.base import AdapterError
from services.operations import OperationService
from services.planning import Planner
from services.records import PackageRequest, PlanConsent

CREATED = datetime(2026, 1, 1, tzinfo=timezone.utc)
DATE = b"Date: Thu, 01 Jan 2026 00:00:00 UTC\n"
EXPIRY = b"Valid-Until: Fri, 02 Jan 2026 00:00:00 UTC\n"


def release(fields=DATE + EXPIRY):
    return (
        b"-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA512\n\n"
        + fields
        + b"-----BEGIN PGP SIGNATURE-----\nOpaque synthetic signature\n"
    )


class APTFreshnessTests(unittest.TestCase):
    def test_utc_gmt_and_numeric_utc_are_supported_without_locale_dependence(self):
        for zone in (b"UTC", b"GMT", b"+0000"):
            with self.subTest(zone=zone):
                self.assertEqual(
                    release_times(release().replace(b"UTC", zone)),
                    (CREATED, CREATED + timedelta(days=1)),
                )
        # Real Ubuntu backports/security releases space-pad single-digit hours.
        self.assertEqual(
            release_times(release(b"Date: Tue, 17 Nov 2026  2:04:59 UTC\n"))[0],
            datetime(2026, 11, 17, 2, 4, 59, tzinfo=timezone.utc),
        )
        self.assertEqual(
            release_times(release(b"Date: Fri,  9 Jan 2026  2:04:59 UTC\n"))[0],
            datetime(2026, 1, 9, 2, 4, 59, tzinfo=timezone.utc),
        )

    def test_future_allowance_and_expiry_boundaries(self):
        for now in (CREATED - timedelta(seconds=10), CREATED + timedelta(days=1)):
            validate_freshness(release(), now)
        for now in (
            CREATED - timedelta(seconds=10, microseconds=1),
            CREATED + timedelta(days=1, microseconds=1),
        ):
            with self.subTest(now=now), self.assertRaises(AdapterError):
                validate_freshness(release(), now)

    def test_static_release_without_expiry_does_not_get_an_invented_maximum_age(self):
        validate_freshness(release(DATE), CREATED + timedelta(days=3650))
        self.assertIsNone(release_times(release(DATE))[1])

    def test_rejects_missing_duplicate_folded_invalid_or_inverted_time_fields(self):
        for fields in (
            EXPIRY,
            DATE + DATE + EXPIRY,
            DATE + EXPIRY + EXPIRY.lower(),
            DATE + b" continuation\n",
            DATE.replace(b"Thu", b"Fri"),
            DATE.replace(b"01 Jan", b"32 Jan"),
            DATE.replace(b"UTC", b"EST"),
            DATE.replace(b"00:00:00", b"00:00:60"),
            DATE.replace(b"Date:", b"Date :"),
            DATE
            + EXPIRY.replace(b"02 Jan", b"31 Dec")
            .replace(b"2026", b"2025")
            .replace(b"Fri", b"Wed"),
            DATE + b"Valid-Until: invalid\n",
        ):
            with self.subTest(fields=fields), self.assertRaises(AdapterError) as caught:
                validate_freshness(release(fields), CREATED)
            self.assertEqual(caught.exception.error.code, ErrorCode.VERIFICATION_FAILED)

    def test_only_signed_body_fields_are_used_and_invalid_clock_is_rejected(self):
        payload = release().replace(b"Hash: SHA512", b"Hash: SHA512\n" + DATE.rstrip())
        self.assertEqual(release_times(payload)[0], CREATED)
        with self.assertRaises(AdapterError):
            validate_freshness(release(), CREATED.replace(tzinfo=None))
        validate_freshness(
            release(), CREATED.astimezone(timezone(timedelta(hours=5, minutes=30)))
        )


class APTFreshnessReviewTests(unittest.TestCase):
    def setUp(self):
        from tests.unit.test_inspection import InspectionTests

        self.fixture = InspectionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        f.release.write_bytes(
            f.release.read_bytes().replace(
                b"Date: Thu, 25 Apr 2024 15:10:33 UTC\n", DATE + EXPIRY
            )
        )
        self.now = CREATED
        f.apt_reader.clock = lambda: self.now

    def snapshot(self, clock):
        f = self.fixture
        return cache_fingerprint(
            f.apt,
            f.runner,
            f.spec("apt"),
            f"{f.index}|main/binary-amd64/Packages|{f.source}:1\n",
            None,
            source_roots=(f.root,),
            key_roots=(f.root,),
            list_roots=(f.cache,),
            helper_path=f.helper,
            clock=clock,
        )

    def test_expiry_after_index_decoding_is_checked_before_returning_evidence(self):
        moments = iter((CREATED, CREATED + timedelta(days=2)))
        with self.assertRaises(AdapterError):
            self.snapshot(lambda: next(moments))
        self.assertTrue(
            any(
                c.args[0].argv[0] == str(self.fixture.helper)
                for c in self.fixture.runner.run.call_args_list
            )
        )

    def test_unverified_signature_is_rejected_before_time_fields_are_used(self):
        self.fixture.gpg_status = "[GNUPG:] BADSIG synthetic\n"

        def forbidden_clock():
            self.fail("Unauthenticated time fields were inspected")

        with self.assertRaises(AdapterError):
            self.snapshot(forbidden_clock)

    def test_expiry_or_clock_rewind_blocks_fresh_and_stale_plans_without_file_changes(
        self,
    ):
        f = self.fixture
        planner = Planner(f.catalog, f.linux, (f.apt_reader,))
        reviewed = planner.preview((PackageRequest("git", OperationAction.UPGRADE),))
        self.assertTrue(reviewed.plan.ready)
        for now in (CREATED + timedelta(days=2), CREATED - timedelta(seconds=11)):
            self.now = now
            fresh = planner.preview((PackageRequest("git", OperationAction.UPGRADE),))
            self.assertFalse(fresh.plan.ready)
            report = OperationService((f.apt_reader,), f.runner).execute(
                reviewed, consent=PlanConsent(reviewed, True, True)
            )
            self.assertEqual(
                report.operations[0].result.error.code, ErrorCode.VERIFICATION_FAILED
            )
            self.assertIsNone(report.operations[0].result.exit_code)
        self.assertTrue(all(not c.args[0].mutates for c in f.runner.run.call_args_list))
        self.now = CREATED + timedelta(hours=1)
        restored = planner.preview((PackageRequest("git", OperationAction.UPGRADE),))
        self.assertTrue(restored.plan.ready)
        self.assertEqual(restored.prepared[0].review, reviewed.prepared[0].review)
