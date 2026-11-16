"""Cached-release guard contracts with real files and a mocked native verifier."""

import unittest
from unittest import mock

from models.errors import ErrorCode, OperationError
from package_managers.apt_cache import cache_fingerprint, cached_releases, valid_status
from package_managers.base import AdapterError


class APTCacheTests(unittest.TestCase):
    def setUp(self):
        from tests.unit.test_inspection import InspectionTests

        self.fixture = InspectionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.entries = (
            f"{self.fixture.index}|main/binary-amd64/Packages|{self.fixture.source}:1\n"
        )

    def snapshot(self, entries=None):
        return cache_fingerprint(
            self.fixture.apt,
            self.fixture.runner,
            self.fixture.spec("apt"),
            self.entries if entries is None else entries,
            None,
            source_roots=(self.fixture.root,),
            key_roots=(self.fixture.root,),
            list_roots=(self.fixture.cache,),
        )

    def test_native_component_index_mapping_is_bounded_and_deduplicated(self):
        self.assertEqual(
            cached_releases(self.entries * 3),
            ((self.fixture.release, self.fixture.source),),
        )
        for entries in (
            "",
            self.entries * 513,
            "unstructured data",
            self.entries.replace("main/binary-amd64/Packages", "../../Packages"),
            self.entries.replace("Packages.lz4", "Packages.zst"),
        ):
            with self.subTest(entries=entries[:60]):
                with self.assertRaises(AdapterError):
                    cached_releases(entries)

    def test_exact_read_only_verifier_uses_only_explicit_bound_inputs(self):
        before = self.snapshot()
        command = self.fixture.runner.run.call_args.args[0]
        self.assertEqual(
            command.argv,
            (
                self.fixture.apt.find("gpgv"),
                "--status-fd",
                "1",
                "--weak-digest",
                "SHA1",
                "--keyring",
                str(self.fixture.key),
                "--",
                str(self.fixture.release),
            ),
        )
        self.assertFalse(command.requires_consent)
        self.assertEqual(len(command.launch_files), 3)
        original = self.fixture.release.read_bytes()
        self.fixture.release.write_bytes(original + b"Updated signed release fixture")
        self.assertNotEqual(self.snapshot(), before)
        self.fixture.release.write_bytes(original)
        self.assertEqual(self.snapshot(), before)

    def test_status_requires_complete_strong_validsig_without_error_markers(self):
        good = self.fixture.gpg_status
        self.assertTrue(valid_status(good))
        for output in (
            "",
            "Good signature",
            "[GNUPG:] GOODSIG arbitrary\n",
            "[GNUPG:] VALIDSIG " + "A" * 40 + "\n",
            good + "[GNUPG:] BADSIG bad\n",
            good + "[GNUPG:] NO_PUBKEY key\n",
            good + "[GNUPG:] UNKNOWN_STATUS\n",
            good.replace(" 10 01", " 2 01"),
        ):
            with self.subTest(output=output[:65]):
                self.assertFalse(valid_status(output))

    def test_zero_exit_bad_signature_and_truncation_are_not_authentication(self):
        for output, truncated in (
            ("[GNUPG:] BADSIG synthetic\n", False),
            (self.fixture.gpg_status, True),
        ):
            self.fixture.gpg_status = output
            self.fixture.truncated = truncated
            with self.assertRaises(AdapterError) as caught:
                self.snapshot()
            self.assertEqual(caught.exception.error.code, ErrorCode.VERIFICATION_FAILED)

    def test_cancellation_and_timeout_keep_their_structured_error(self):
        for code in (ErrorCode.CANCELLED, ErrorCode.TIMEOUT):
            self.fixture.error = OperationError(code, "Synthetic interruption")
            with self.assertRaises(AdapterError) as caught:
                self.snapshot()
            self.assertEqual(caught.exception.error.code, code)

    def test_changed_input_during_native_verification_is_rejected(self):
        original = self.fixture.runner.run.side_effect

        def changed(command, **kwargs):
            result = original(command, **kwargs)
            self.fixture.release.write_bytes(b"Changed while verifier was running")
            return result

        self.fixture.runner.run.side_effect = changed
        with self.assertRaises(AdapterError):
            self.snapshot()

    def test_mixed_stanza_keys_and_armored_keys_are_not_silently_combined(self):
        original = self.fixture.source.read_text()
        other = self.fixture.root / "other.gpg"
        other.write_bytes(self.fixture.key.read_bytes())
        self.fixture.source.write_text(original + f"\nTypes: deb\nSigned-By: {other}\n")
        with self.assertRaises(AdapterError):
            self.snapshot()
        self.fixture.source.write_text(original.replace("archive.gpg", "archive.asc"))
        with self.assertRaises(AdapterError):
            self.snapshot()
        self.fixture.runner.run.assert_not_called()

    def test_missing_verifier_and_outside_cache_paths_fail_before_dispatch(self):
        finder = self.fixture.apt.find
        self.fixture.apt.find = lambda name: None if name == "gpgv" else finder(name)
        with self.assertRaises(AdapterError) as caught:
            self.snapshot()
        self.assertEqual(caught.exception.error.code, ErrorCode.MISSING_BACKEND)
        self.fixture.apt.find = finder
        outside = self.fixture.root / self.fixture.release.name
        outside.write_bytes(self.fixture.release.read_bytes())
        with self.assertRaises(AdapterError):
            self.snapshot(
                self.entries.replace(str(self.fixture.cache), str(self.fixture.root))
            )
        self.fixture.runner.run.assert_not_called()

    def test_total_read_budget_stops_before_all_large_key_inputs_are_read(self):
        import package_managers.apt_cache as cache

        keys = tuple(self.fixture.root / f"key{index}.gpg" for index in range(9))
        for key in keys:
            key.write_bytes(b"Synthetic public key")
        self.fixture.source.write_text(
            "Types: deb\nSigned-By: " + " ".join(str(key) for key in keys) + "\n"
        )
        original = cache._read
        observed = []
        data = b"x" * (2 * 1024 * 1024)

        def large(path, roots):
            if path in keys:
                observed.append(path)
                return data
            return original(path, roots)

        with mock.patch.object(cache, "_read", side_effect=large):
            with self.assertRaises(AdapterError):
                self.snapshot()
        self.assertLess(len(observed), len(keys))
        self.fixture.runner.run.assert_not_called()
