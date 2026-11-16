"""Offline contracts for the explicitly approved native authentication profile."""

import tempfile
import unittest
from pathlib import Path

from models.errors import ErrorCode, OperationError
from models.planning import utc_now
from scripts.apt_auth_checks import (
    has_indexes,
    prepare_scope,
    refresh_command,
    require_signature_rejection,
)
from scripts.apt_origin_checks import PrivateListsRunner
from system.commands import Approval, CommandResult
from system.identity import IdentityError, revalidate


class APTAuthChecksTests(unittest.TestCase):
    def setUp(self):
        from tests.unit.test_native_qualification import NativeQualificationTests

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.fixture = NativeQualificationTests().apt_fixture(self.root, "cmake")
        key = self.root / "synthetic.gpg"
        key.write_bytes(b"Synthetic public key fixture")
        self.scope = prepare_scope(self.root / "private", key)

    def test_refresh_is_exact_approved_and_binds_configuration_and_key(self):
        command = refresh_command(self.fixture.adapter, self.scope)
        self.assertEqual(
            command.argv,
            (self.fixture.adapter.find("apt-get"), "--error-on=any", "update"),
        )
        self.assertTrue(command.requires_consent)
        self.assertTrue(Approval(command).permits(command))
        self.assertEqual(
            dict(command.environment)["APT_CONFIG"], str(self.scope.bootstrap)
        )
        self.assertEqual(len(command.launch_files), 4)
        revalidate(command, environment=dict(command.environment))
        (self.scope.keys / "archive.gpg").write_bytes(b"Changed public key")
        with self.assertRaises(IdentityError):
            revalidate(command, environment=dict(command.environment))

    def test_private_bootstrap_isolates_native_config_trust_state_and_logs(self):
        text = self.scope.bootstrap.read_text()
        for setting, suffix in (
            ("Dir::Etc::parts", "config/parts"),
            ("Dir::Etc::main", "config/main.conf"),
            ("Dir::Etc::trustedparts", "config/trusted.d"),
            ("Dir::State::lists", "state/lists"),
            ("Dir::Cache::archives", "cache/archives"),
            ("Dir::Log", "log"),
        ):
            self.assertIn(f'{setting} "{(self.scope.root / suffix).as_posix()}";', text)
        self.assertFalse(has_indexes(self.scope))
        self.assertEqual(tuple((self.scope.root / "config/parts").iterdir()), ())
        manifest = (self.scope.sources / "sources.list.d/ubuntu.sources").read_text()
        self.assertIn("URIs: https://archive.ubuntu.com/ubuntu/", manifest)
        self.assertIn("Architectures: amd64", manifest)
        self.assertNotIn("Trusted:", manifest)
        self.assertNotIn("AllowInsecure", text)
        self.assertNotIn("Verify-Peer", text)

    def test_existing_scope_cannot_supply_a_stale_cache(self):
        marker = self.scope.lists / "existing_Packages"
        marker.write_bytes(b"Stale synthetic index")
        with self.assertRaises(FileExistsError):
            prepare_scope(self.scope.root, self.root / "synthetic.gpg")
        self.assertEqual(marker.read_bytes(), b"Stale synthetic index")

    def test_only_complete_signature_specific_native_failure_counts(self):
        command = refresh_command(self.fixture.adapter, self.scope)
        now = utc_now()
        require_signature_rejection(
            CommandResult(
                command.preview(),
                now,
                now,
                100,
                stderr="NO_PUBKEY synthetic",
                error=OperationError(ErrorCode.COMMAND_FAILED, "Rejected signature."),
            )
        )
        for exit_code, stderr, truncated, error in (
            (0, "NO_PUBKEY", False, None),
            (100, "Connection failed", False, None),
            (100, "NO_PUBKEY", True, None),
            (
                100,
                "NO_PUBKEY",
                False,
                OperationError(ErrorCode.NETWORK, "Network unavailable."),
            ),
        ):
            with self.subTest(exit_code=exit_code, stderr=stderr, truncated=truncated):
                with self.assertRaises(ValueError):
                    require_signature_rejection(
                        CommandResult(
                            command.preview(),
                            now,
                            now,
                            exit_code,
                            stderr=stderr,
                            output_truncated=truncated,
                            error=error,
                        )
                    )

    def test_reads_share_bootstrap_but_package_mutation_remains_refused(self):
        runner = PrivateListsRunner(
            self.fixture.runner,
            self.scope.lists,
            self.scope.sources,
            self.scope.bootstrap,
        )
        runner.run(self.fixture.adapter.version())
        delegated = self.fixture.runner.run.call_args.args[0]
        self.assertEqual(
            dict(delegated.environment)["APT_CONFIG"], str(self.scope.bootstrap)
        )
        self.assertIn(f"Dir::State::lists={self.scope.lists}", delegated.argv)
        with self.assertRaises(ValueError):
            runner.run(refresh_command(self.fixture.adapter, self.scope))
        self.assertEqual(runner.mutation_attempts, 1)

    def test_unencodable_private_path_is_refused_before_creation(self):
        invalid = self.root / 'unsafe"path'
        with self.assertRaises(ValueError):
            prepare_scope(invalid, self.root / "synthetic.gpg")
        self.assertFalse(invalid.exists())
