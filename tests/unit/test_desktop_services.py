"""Qt-independent desktop aggregation, source identity, and update semantics."""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from models.errors import ErrorCode
from models.planning import OperationAction
from tests.fixtures.desktop import DesktopFixture


class DesktopServiceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.fixture = DesktopFixture(Path(directory.name))
        self.services = self.fixture.services
        patch = mock.patch("services.inventory.shutil.which", return_value=None)
        patch.start()
        self.addCleanup(patch.stop)

    def test_refresh_matches_native_identity_and_keeps_unmapped_packages(self):
        self.fixture.install()
        self.fixture.install("unmapped-native-tool")
        snapshot = self.services.refresh(threading.Event())
        self.assertEqual(snapshot.installed[0].package_id, "git")
        self.assertIsNotNone(snapshot.installed[0].mapping)
        self.assertIsNone(snapshot.installed[1].package_id)
        self.assertFalse(self.fixture.mutations)

    def test_unreviewed_installed_source_is_not_associated_with_curated_mapping(self):
        self.fixture.install(source="unreviewed-homebrew-tap")
        row = self.services.refresh(threading.Event()).installed[0]
        self.assertIsNone(row.mapping)
        self.assertIsNone(row.package_id)
        self.assertEqual(row.source_id, "unreviewed-homebrew-tap")

    def test_unknown_installed_origin_is_displayed_without_invented_provenance(self):
        self.fixture.install(source=None)
        row = self.services.refresh(threading.Event()).installed[0]
        self.assertIsNone(row.source_id)
        self.assertEqual(row.package_id, "git")

    def test_update_check_preserves_source_and_never_executes_mutations(self):
        self.fixture.install()
        result = self.services.updates(
            self.services.refresh(threading.Event()), threading.Event()
        )
        self.assertEqual(len(result.candidates), 1)
        candidate = result.candidates[0]
        self.assertEqual(
            (candidate.package_id, candidate.installed, candidate.candidate),
            ("git", ("1.0",), "2.0"),
        )
        self.assertEqual(candidate.spec.source.id, "homebrew-core")
        self.assertFalse(self.fixture.mutations)

    def test_up_to_date_and_unmapped_tools_do_not_become_updates(self):
        self.fixture.install(version="2.0")
        self.fixture.install("unmapped-native-tool")
        result = self.services.updates(
            self.services.refresh(threading.Event()), threading.Event()
        )
        self.assertFalse(result.candidates)
        self.assertFalse(result.errors)

    def test_failed_inventory_is_not_a_clean_update_check(self):
        self.fixture.inventory_error = ErrorCode.NETWORK
        result = self.services.updates(
            self.services.refresh(threading.Event()), threading.Event()
        )
        self.assertTrue(result.errors)
        self.assertIn("could not be checked", result.errors[-1])

    def test_cancelled_update_scan_does_not_mutate_or_invent_completion(self):
        self.fixture.install()
        snapshot = self.services.refresh(threading.Event())
        cancel = threading.Event()
        cancel.set()
        result = self.services.updates(snapshot, cancel)
        self.assertTrue(result.cancelled)
        self.assertFalse(result.candidates)
        self.assertFalse(self.fixture.mutations)

    def test_capabilities_and_missing_backend_are_explicit(self):
        spec = next(
            s
            for s in self.services.catalog.get("git").install_specs
            if s.backend == "homebrew"
        )
        self.assertFalse(self.services.version_selection(spec, OperationAction.INSTALL))
        self.assertTrue(self.services.version_selection(spec, OperationAction.VERIFY))
        self.fixture.adapter.find = lambda name: None
        self.assertIsNotNone(self.services.action_reason(spec, OperationAction.INSTALL))
