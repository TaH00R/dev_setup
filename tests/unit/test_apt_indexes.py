"""Signed index linkage uses mocked decoding; ordinary tests never run APT."""

import hashlib
import unittest
from dataclasses import replace

from models.errors import ErrorCode
from package_managers.apt_indexes import index_fingerprint, release_checksums
from package_managers.base import AdapterError


class APTIndexTests(unittest.TestCase):
    def setUp(self):
        from tests.unit.test_inspection import InspectionTests

        self.fixture = InspectionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def check_index(self):
        f = self.fixture
        return index_fingerprint(
            f.apt,
            f.runner,
            f.spec("apt"),
            ((f.index, "main/binary-amd64/Packages", f.release, f.source),),
            {f.release: f.release.read_bytes()},
            None,
            list_roots=(f.cache,),
            helper_path=f.helper,
        )

    def test_signed_uncompressed_size_and_hash_drive_a_bound_read_only_decoder(self):
        self.check_index()
        f = self.fixture
        command = f.runner.run.call_args.args[0]
        self.assertEqual(
            command.argv,
            (
                str(f.helper),
                "-o",
                "Apt-Helper::Cat-File::Compress=",
                "cat-file",
                "--",
                str(f.index),
            ),
        )
        self.assertEqual(command.stdout_sha256_limit, len(f.index.read_bytes()))
        self.assertFalse(command.requires_consent)
        self.assertEqual(len(command.launch_files), 2)

    def test_checksum_parser_only_accepts_unique_sha256_rows_inside_signed_body(self):
        content = self.fixture.release.read_bytes()
        rows = release_checksums(content)
        digest, size = rows["main/binary-amd64/Packages"]
        self.assertEqual(
            digest, hashlib.sha256(self.fixture.index.read_bytes()).hexdigest()
        )
        self.assertEqual(size, len(self.fixture.index.read_bytes()))
        row = f" {digest} {size} main/binary-amd64/Packages\n".encode()
        for altered in (
            content.replace(b"SHA256:", b"SHA1:"),
            content.replace(row, row + row),
            content.replace(row, b" invalid 1 main/binary-amd64/Packages\n"),
            content.replace(row, row.replace(b"main/binary", b"../binary")),
            b"unsigned header\n" + row,
        ):
            with self.subTest(length=len(altered)):
                with self.assertRaises(AdapterError):
                    release_checksums(altered)

    def test_altered_index_is_rejected_even_when_release_and_metadata_stay_valid(self):
        self.fixture.index.write_bytes(b"Different package data, same trusted release")
        with self.assertRaises(AdapterError) as caught:
            self.check_index()
        self.assertEqual(caught.exception.error.code, ErrorCode.VERIFICATION_FAILED)

    def test_zero_exit_without_complete_digest_or_size_is_not_verification(self):
        original = self.fixture.runner.run.side_effect
        for change in (
            {"stdout_sha256": None},
            {"stdout_size": None},
            {"stdout_size": 0},
            {"output_truncated": True},
        ):

            def incomplete(command, **kwargs):
                return replace(original(command, **kwargs), **change)

            self.fixture.runner.run.side_effect = incomplete
            with self.subTest(change=change):
                with self.assertRaises(AdapterError):
                    self.check_index()

    def test_missing_helper_and_signed_checksum_fail_without_native_dispatch(self):
        self.fixture.helper.unlink()
        with self.assertRaises(AdapterError) as caught:
            self.check_index()
        self.assertEqual(caught.exception.error.code, ErrorCode.MISSING_BACKEND)
        self.fixture.helper.write_bytes(b"Synthetic helper")
        self.fixture.release.write_bytes(
            self.fixture.release.read_bytes().replace(
                b"main/binary-amd64/Packages", b"other/binary-amd64/Packages"
            )
        )
        with self.assertRaises(AdapterError):
            self.check_index()
        self.fixture.runner.run.assert_not_called()

    def test_input_changed_while_decoder_runs_is_rejected(self):
        original = self.fixture.runner.run.side_effect

        def changed(command, **kwargs):
            result = original(command, **kwargs)
            self.fixture.index.write_bytes(b"Changed during native decoding")
            return result

        self.fixture.runner.run.side_effect = changed
        with self.assertRaises(AdapterError):
            self.check_index()
