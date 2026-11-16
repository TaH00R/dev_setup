"""Offline fault construction and owned loopback-server contracts; no native APT."""

import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener

from models.errors import ErrorCode, OperationError
from models.planning import CommandPreview, utc_now
from scripts.apt_auth_checks import has_indexes, require_signature_rejection
from scripts.apt_tamper_checks import (
    prepare_tampered_scope,
    serve_release,
    tamper_release,
)
from system.commands import CommandResult

RELEASE = (
    b"-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA512\n\n"
    b"Origin: Ubuntu\nCodename: noble\nDescription: Ubuntu Noble\n"
    b"-----BEGIN PGP SIGNATURE-----\nSynthetic opaque signature\n"
    b"-----END PGP SIGNATURE-----\n"
)


class APTTamperChecksTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.key = self.root / "synthetic.gpg"
        self.key.write_bytes(b"Synthetic public key; never verified")

    def test_exactly_one_description_byte_changes_and_signature_is_preserved(self):
        altered = tamper_release(RELEASE)
        self.assertEqual(len(altered), len(RELEASE))
        self.assertEqual(sum(a != b for a, b in zip(RELEASE, altered)), 1)
        self.assertIn(b"\nOrigin: Ubuntu\nCodename: noble\n", altered)
        signature = b"-----BEGIN PGP SIGNATURE-----"
        self.assertEqual(altered.split(signature)[1], RELEASE.split(signature)[1])
        self.assertIn(b"Description: Xbuntu Noble\n", altered)

    def test_malformed_or_unreviewed_release_cannot_be_used_as_fault_evidence(self):
        for payload in (
            b"",
            RELEASE.replace(b"PGP SIGNED MESSAGE", b"NOT SIGNED DATA"),
            RELEASE.replace(b"Description: Ubuntu Noble\n", b""),
            RELEASE.replace(b"Origin: Ubuntu", b"Origin: Unreviewed"),
            RELEASE + b"x" * (2 * 1024 * 1024),
        ):
            with self.subTest(length=len(payload)):
                with self.assertRaises(ValueError):
                    tamper_release(payload)

    def test_loopback_server_serves_only_fixed_bytes_and_closes(self):
        payload = tamper_release(RELEASE)
        opener = build_opener(ProxyHandler({}))
        with serve_release(payload) as endpoint:
            base = f"http://127.0.0.1:{endpoint.port}"
            with opener.open(base + "/dists/noble/InRelease", timeout=2) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), payload)
            with self.assertRaises(HTTPError) as caught:
                opener.open(base + "/../../etc/passwd", timeout=2)
            self.assertEqual(caught.exception.code, 404)
            caught.exception.close()
            self.assertEqual(endpoint.release_gets, 1)
            self.assertEqual(endpoint.unexpected_gets, 1)
        self.assertTrue(endpoint.closed)

    def test_badsig_evidence_cannot_be_substituted_by_missing_key_or_network_error(
        self,
    ):
        now = utc_now()
        preview = CommandPreview(("apt-get", "update"), "Synthetic refresh")
        result = CommandResult(preview, now, now, 100, stderr="BADSIG synthetic")
        require_signature_rejection(result, marker="BADSIG")
        for exit_code, stderr, truncated, error in (
            (100, "NO_PUBKEY", False, None),
            (100, "Connection refused", False, None),
            (0, "BADSIG", False, None),
            (100, "BADSIG", True, None),
            (100, "BADSIG", False, OperationError(ErrorCode.NETWORK, "Network error")),
        ):
            with self.subTest(exit_code=exit_code, stderr=stderr, truncated=truncated):
                with self.assertRaises(ValueError):
                    require_signature_rejection(
                        CommandResult(
                            preview,
                            now,
                            now,
                            exit_code,
                            stderr=stderr,
                            output_truncated=truncated,
                            error=error,
                        ),
                        marker="BADSIG",
                    )

    def test_local_scope_uses_only_loopback_and_an_empty_private_cache(self):
        scope = prepare_tampered_scope(self.root / "private", self.key, 12345)
        manifest = (scope.sources / "sources.list.d/ubuntu.sources").read_text()
        self.assertIn("URIs: http://127.0.0.1:12345/\n", manifest)
        self.assertNotIn("Trusted:", manifest)
        self.assertIn(
            'Acquire::http::Proxy::127.0.0.1 "DIRECT";', scope.bootstrap.read_text()
        )
        self.assertFalse(has_indexes(scope))

    def test_invalid_port_is_refused_before_creating_a_scope(self):
        root = self.root / "invalid"
        for port in (0, -1, 65536, True, "80"):
            with self.subTest(port=port):
                with self.assertRaises(ValueError):
                    prepare_tampered_scope(root, self.key, port)
                self.assertFalse(root.exists())
