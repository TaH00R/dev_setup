"""Origin fault qualification uses real planning/inspection and fake native output."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from models.errors import ErrorCode, OperationError
from models.planning import utc_now
from scripts.apt_origin_checks import (
    PrivateListsRunner,
    copy_lists,
    copy_sources,
    exercise_apt_origins,
    exercise_apt_sources,
)
from system.commands import CommandResult


class APTOriginProfileTests(unittest.TestCase):
    def setUp(self):
        from tests.unit.test_native_qualification import NativeQualificationTests

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.fixture = NativeQualificationTests().apt_fixture(self.root, "cmake")
        self.original = self.root / "original"
        self.original.mkdir()
        self.content = b"Signed cache fixture\nOrigin: Ubuntu\nCodename: noble\n"
        (self.original / "archive_InRelease").write_bytes(self.content)
        self.lists = self.root / "lists"
        copy_lists(self.original, self.lists)
        self.break_policy = False
        self.ignore_sources = False
        self.native_source_error = False
        self.sources = None
        self.fixture.runner.run.side_effect = self.run_read
        # This harness fixture models cache/parser behavior. Real trust-file
        # snapshots and stale-approval rejection have separate core regressions.
        trust = mock.patch(
            "package_managers.inspection.trust_fingerprint",
            return_value="fixture-trust",
        )
        trust.start()
        self.addCleanup(trust.stop)

    def run_read(self, command, **kwargs):
        self.assertFalse(command.mutates)
        argv = command.argv
        if (
            self.sources
            and not self.ignore_sources
            and b"dev-setup.invalid"
            in (self.sources / "sources.list.d" / "ubuntu.sources").read_bytes()
            and Path(argv[0]).name in {"apt-cache", "apt-get"}
        ):
            now = utc_now()
            return CommandResult(
                command.preview(),
                now,
                now,
                0 if argv[-1] == "policy" else 100,
                "Package files:\n 100 /var/lib/dpkg/status\n     release a=now\nPinned packages:\n",
                error=OperationError(
                    ErrorCode.COMMAND_FAILED, "Native target release is unavailable."
                )
                if self.native_source_error and argv[-1] != "policy"
                else None,
            )
        content = (self.lists / "archive_InRelease").read_bytes()
        origin = "DevSetupUnreviewed" if b"DevSetupUnreviewed" in content else "Ubuntu"
        if Path(argv[0]).name == "dpkg-query":
            output = ""
        elif argv[-1] == "policy":
            output = (
                "Package files:\n 500 http://archive.ubuntu.com/ubuntu noble/main amd64 Packages\n"
                f"     release v=24.04,o={origin},a=noble,n=noble,l=Ubuntu,c=main,b=amd64\n"
            )
            if self.break_policy and origin != "Ubuntu":
                output = "Unsupported metadata"
        elif "policy" in argv:
            output = (
                "cmake:\n  Installed: (none)\n  Candidate: 3.28.3-1build7\n  Version table:\n"
                "     3.28.3-1build7 990\n"
                "        990 http://archive.ubuntu.com/ubuntu noble/main amd64 Packages\n"
            )
        elif "show" in argv:
            output = "Package: cmake\nVersion: 3.28.3-1build7\nArchitecture: amd64\n"
        elif "--simulate" in argv:
            output = "Inst cmake (3.28.3-1build7 Ubuntu:24.04/noble [amd64])\n"
        else:
            output = ""
        now = utc_now()
        return CommandResult(command.preview(), now, now, 0, output)

    def test_altered_origins_block_new_and_reviewed_plans_and_restore(self):
        checks = {}
        exercise_apt_origins(self.fixture.services, checks, self.lists)
        self.assertEqual(checks["phase"], "complete")
        self.assertEqual(checks["fresh_plan_altered_origin"], "rejected")
        self.assertEqual(
            checks["reviewed_plan_altered_origin"], "rejected_before_dispatch"
        )
        self.assertEqual(checks["restored_origin_review"], "passed")
        self.assertEqual((self.lists / "archive_InRelease").read_bytes(), self.content)
        self.assertEqual(
            (self.original / "archive_InRelease").read_bytes(), self.content
        )

    def test_native_reader_must_witness_the_injected_origin(self):
        self.break_policy = True
        checks = {}
        with self.assertRaises(ValueError):
            exercise_apt_origins(self.fixture.services, checks, self.lists)
        self.assertNotIn("cached_origin_tampering", checks)
        self.assertEqual((self.lists / "archive_InRelease").read_bytes(), self.content)

    def test_missing_release_metadata_cannot_count_as_evidence(self):
        (self.lists / "archive_InRelease").write_bytes(b"Origin: Other\n")
        checks = {}
        with self.assertRaises(ValueError):
            exercise_apt_origins(self.fixture.services, checks, self.lists)
        self.assertNotIn("cached_origin_tampering", checks)

    def test_all_mutation_dispatch_is_refused(self):
        runner = PrivateListsRunner(self.fixture.runner, self.lists)
        spec = next(
            s
            for s in self.fixture.services.catalog.get("cmake").install_specs
            if s.backend == "apt"
        )
        with self.assertRaises(ValueError):
            runner.run(self.fixture.adapter.install(spec))
        self.assertEqual(runner.mutation_attempts, 1)
        self.fixture.runner.run.assert_not_called()

    def test_only_apt_reads_are_redirected_and_shared_binary_caches_are_disabled(self):
        runner = PrivateListsRunner(self.fixture.runner, self.lists)
        runner.run(self.fixture.adapter.version())
        argv = self.fixture.runner.run.call_args.args[0].argv
        self.assertIn(f"Dir::State::lists={self.lists}", argv)
        self.assertIn("Dir::Cache::pkgcache=", argv)
        self.assertIn("Dir::Cache::srcpkgcache=", argv)
        command = self.fixture.adapter.list_installed()
        runner.run(command)
        self.assertEqual(self.fixture.runner.run.call_args.args[0], command)

    def test_cache_copy_refuses_symlinks(self):
        with mock.patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaises(ValueError):
                copy_lists(self.original, self.root / "rejected-copy")

    def source_fixture(self):
        source = self.root / "source-original"
        source.mkdir()
        parts = source / "sources.list.d"
        parts.mkdir()
        content = b"Types: deb\nURIs: http://archive.ubuntu.com/ubuntu\nSuites: noble\nComponents: main\nSigned-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
        (parts / "ubuntu.sources").write_bytes(content)
        (parts / "ignored.conf").write_bytes(b"Not a source manifest")
        (source / "auth.conf").write_bytes(b"Synthetic credential; never copied")
        (source / "sources.list").write_bytes(b"# Legacy source manifest\n")
        self.sources = self.root / "source-copy"
        copy_sources(source, self.sources)
        return source, content

    def test_repository_url_change_blocks_plans_and_restores_originals(self):
        source, content = self.source_fixture()
        checks = {}
        exercise_apt_sources(self.fixture.services, checks, self.lists, self.sources)
        self.assertEqual(checks["phase"], "complete")
        self.assertEqual(checks["fresh_plan_changed_repository"], "rejected")
        self.assertEqual(
            checks["reviewed_plan_changed_repository"], "rejected_before_dispatch"
        )
        self.assertEqual(checks["restored_source_review"], "passed")
        self.assertEqual(
            (self.sources / "sources.list.d" / "ubuntu.sources").read_bytes(), content
        )
        self.assertEqual(
            (source / "sources.list.d" / "ubuntu.sources").read_bytes(), content
        )
        self.assertFalse((self.sources / "auth.conf").exists())
        self.assertFalse((self.sources / "sources.list.d" / "ignored.conf").exists())
        commands = [call.args[0] for call in self.fixture.runner.run.call_args_list]
        apt_commands = [
            c for c in commands if Path(c.argv[0]).name in {"apt-get", "apt-cache"}
        ]
        self.assertTrue(apt_commands)
        self.assertTrue(
            all(
                f"Dir::Etc::sourcelist={self.sources / 'sources.list'}" in c.argv
                and f"Dir::Etc::sourceparts={self.sources / 'sources.list.d'}" in c.argv
                for c in apt_commands
            )
        )

    def test_source_change_requires_native_witness_and_restores_after_failure(self):
        _, content = self.source_fixture()
        self.ignore_sources = True
        checks = {}
        with self.assertRaises(ValueError):
            exercise_apt_sources(
                self.fixture.services, checks, self.lists, self.sources
            )
        self.assertNotIn("configured_repository_change", checks)
        self.assertEqual(
            (self.sources / "sources.list.d" / "ubuntu.sources").read_bytes(), content
        )

    def test_native_command_rejection_is_preserved_without_mutation_dispatch(self):
        self.source_fixture()
        self.native_source_error = True
        checks = {}
        exercise_apt_sources(self.fixture.services, checks, self.lists, self.sources)
        self.assertEqual(checks["source_rejection_code"], "command_failed")
        self.assertEqual(checks["native_mutation_dispatch"], "none")

    def test_source_copy_refuses_links(self):
        source, _ = self.source_fixture()
        with mock.patch.object(Path, "is_symlink", return_value=True):
            with self.assertRaises(ValueError):
                copy_sources(source, self.root / "rejected-source-copy")
