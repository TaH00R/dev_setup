"""Signature, origin, rollback, and download checks use ephemeral signing keys."""

import hashlib
import importlib.util
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from models.platform import Architecture, OperatingSystem, Platform
from services.updates import (
    HTTPSTransport,
    TrustedChannel,
    UpdateConsent,
    UpdateError,
    UpdateService,
    verify_manifest,
)

CRYPTO = importlib.util.find_spec("cryptography") is not None


class FakeTransport:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def chunks(self, url, limit, cancel):
        self.calls.append(url)
        value = self.data[url]
        for start in range(0, len(value), 5):
            if cancel.is_set():
                raise UpdateError("Update transfer was cancelled.")
            yield value[start : start + 5]


class DisabledUpdateTests(unittest.TestCase):
    def test_slow_trickle_cannot_fill_a_large_buffer_past_the_deadline(self):
        channel = TrustedChannel(
            "https://releases.example.test/release.json",
            "https://releases.example.test/release.sig",
            b"x" * 32,
            ("https://releases.example.test/files/",),
        )
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = channel.manifest_url
        response.headers = {}
        response.read1.return_value = b"trickle"
        opener = mock.Mock()
        opener.open.return_value = response
        with (
            mock.patch(
                "services.updates.urllib.request.build_opener", return_value=opener
            ),
            mock.patch("services.updates.time.monotonic", side_effect=(0, 0, 121)),
        ):
            with self.assertRaisesRegex(UpdateError, "time budget"):
                list(
                    HTTPSTransport(channel).chunks(
                        channel.manifest_url, 65536, threading.Event()
                    )
                )
        response.read1.assert_called_once_with(65536)
        response.read.assert_not_called()

    def test_cancellation_after_available_byte_read_is_not_yielded(self):
        channel = TrustedChannel(
            "https://releases.example.test/release.json",
            "https://releases.example.test/release.sig",
            b"x" * 32,
            ("https://releases.example.test/files/",),
        )
        cancel = threading.Event()
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.geturl.return_value = channel.manifest_url
        response.headers = {}

        def read(_):
            cancel.set()
            return b"cancelled-content"

        response.read1.side_effect = read
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch(
            "services.updates.urllib.request.build_opener", return_value=opener
        ):
            with self.assertRaisesRegex(UpdateError, "cancelled"):
                list(
                    HTTPSTransport(channel).chunks(channel.manifest_url, 65536, cancel)
                )

    def test_no_production_feed_or_key_is_invented(self):
        result = UpdateService().check(
            "0.1.0.dev0", Platform(OperatingSystem.WINDOWS, Architecture.X86_64)
        )
        self.assertEqual(result.status, "disabled")
        self.assertIsNone(result.offer)

    def test_channel_requires_pinned_https_origins_and_real_key_size(self):
        for manifest in (
            "http://example.org/feed",
            "https://user:pass@example.org/feed",
            "https://example.org/feed#fragment",
        ):
            with self.assertRaises(UpdateError):
                TrustedChannel(
                    manifest,
                    "https://example.org/feed.sig",
                    b"x" * 32,
                    ("https://example.org/files/",),
                )
        with self.assertRaises(UpdateError):
            TrustedChannel(
                "https://example.org/feed",
                "https://different.org/feed.sig",
                b"x" * 32,
                ("https://example.org/files/",),
            )


@unittest.skipUnless(
    CRYPTO, "Install requirements-security.txt for signed update tests"
)
class SignedUpdateTests(unittest.TestCase):
    def setUp(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        self.key = Ed25519PrivateKey.generate()
        public = self.key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        self.channel = TrustedChannel(
            "https://releases.example.org/latest.json",
            "https://releases.example.org/latest.sig",
            public,
            ("https://releases.example.org/files/",),
            1,
        )
        self.content = b"synthetic release artifact; never executed"
        self.document = {
            "schema_version": 1,
            "version": "0.1.0",
            "sequence": 2,
            "artifacts": [
                {
                    "os": "windows",
                    "architecture": "x86_64",
                    "filename": "DevSetup-0.1.0.zip",
                    "url": "https://releases.example.org/files/DevSetup-0.1.0.zip",
                    "sha256": hashlib.sha256(self.content).hexdigest(),
                    "size": len(self.content),
                }
            ],
        }
        self.target = Platform(OperatingSystem.WINDOWS, Architecture.X86_64)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def service(self):
        data = json.dumps(self.document, separators=(",", ":")).encode()
        signature = self.key.sign(data)
        transport = FakeTransport(
            {
                self.channel.manifest_url: data,
                self.channel.signature_url: signature,
                self.document["artifacts"][0]["url"]: self.content,
            }
        )
        return UpdateService(self.channel, transport), transport

    def test_source_preview_version_can_receive_a_numbered_signed_release(self):
        service, _ = self.service()
        check = service.check("0.1.0.dev0", self.target)
        self.assertEqual(check.status, "available")
        self.assertEqual(check.offer.release.version, "0.1.0")

    def test_current_newer_and_unsupported_platforms_do_not_get_install_offers(self):
        service, _ = self.service()
        self.assertEqual(service.check("0.1.0", self.target).status, "current")
        self.assertEqual(service.check("0.2.0", self.target).status, "current")
        self.assertEqual(
            service.check(
                "0.0.1", Platform(OperatingSystem.LINUX, Architecture.ARM64)
            ).status,
            "unsupported",
        )

    def test_signature_tampering_and_wrong_pinned_key_are_rejected(self):
        service, transport = self.service()
        transport.data[self.channel.manifest_url] += b" "
        with self.assertRaises(UpdateError):
            service.check("0.0.1", self.target)
        service, transport = self.service()
        other = replace(self.channel, public_key=b"z" * 32)
        with self.assertRaises(UpdateError):
            UpdateService(other, transport).check("0.0.1", self.target)

    def test_signed_but_stale_sequence_cannot_be_replayed(self):
        service, _ = self.service()
        with self.assertRaises(UpdateError):
            service.check("0.0.1", self.target, current_sequence=3)
        self.document["sequence"] = 0
        service, _ = self.service()
        with self.assertRaises(UpdateError):
            service.check("0.0.1", self.target)

    def test_signed_metadata_still_rejects_unknown_fields_and_unsafe_urls(self):
        for mutation in ("origin", "path", "schema", "size", "preview"):
            original = json.loads(json.dumps(self.document))
            if mutation == "origin":
                self.document["artifacts"][0]["url"] = (
                    "https://untrusted.example.org/evil.zip"
                )
            elif mutation == "path":
                self.document["artifacts"][0]["filename"] = "../outside.zip"
            elif mutation == "schema":
                self.document["hook"] = "arbitrary executable input"
            elif mutation == "size":
                self.document["artifacts"][0]["size"] = True
            else:
                self.document["version"] = "0.2.0.dev0"
            service, _ = self.service()
            with self.assertRaises(UpdateError):
                service.check("0.0.1", self.target)
            self.document = original

    def test_duplicate_json_keys_cannot_change_signed_interpretation(self):
        data = b'{"schema_version":1,"schema_version":2}'
        with self.assertRaises(UpdateError):
            verify_manifest(data, self.key.sign(data), self.channel)

    def test_exact_download_consent_is_required(self):
        service, transport = self.service()
        offer = service.check("0.0.1", self.target).offer
        before = len(transport.calls)
        for consent in (None, UpdateConsent(offer, False)):
            with self.assertRaises(UpdateError):
                service.download(offer, Path(self.directory.name), consent)
        self.assertEqual(len(transport.calls), before)

    def test_successful_download_is_verified_and_never_executed_or_overwritten(self):
        service, _ = self.service()
        offer = service.check("0.0.1", self.target).offer
        consent = UpdateConsent(offer, True)
        path = service.download(offer, Path(self.directory.name), consent)
        self.assertEqual(path.read_bytes(), self.content)
        self.assertFalse(list(Path(self.directory.name).glob("*.download")))
        with self.assertRaises(UpdateError):
            service.download(offer, Path(self.directory.name), consent)

    def test_bad_checksum_size_and_cancellation_leave_no_artifact(self):
        service, transport = self.service()
        offer = service.check("0.0.1", self.target).offer
        transport.data[offer.artifact.url] = b"tampered"
        with self.assertRaises(UpdateError):
            service.download(
                offer, Path(self.directory.name), UpdateConsent(offer, True)
            )
        self.assertFalse(list(Path(self.directory.name).iterdir()))
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(UpdateError):
            service.download(
                offer, Path(self.directory.name), UpdateConsent(offer, True), cancel
            )
        self.assertFalse(list(Path(self.directory.name).iterdir()))

    def test_forged_offer_is_reverified_before_transfer(self):
        service, transport = self.service()
        offer = service.check("0.0.1", self.target).offer
        forged = replace(offer, artifact=replace(offer.artifact, sha256="0" * 64))
        calls = len(transport.calls)
        with self.assertRaises(UpdateError):
            service.download(
                forged, Path(self.directory.name), UpdateConsent(forged, True)
            )
        self.assertEqual(len(transport.calls), calls)
