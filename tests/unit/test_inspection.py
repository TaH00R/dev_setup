"""Offline native format contracts and conservative failure behavior."""

import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from models.errors import ErrorCode, OperationError
from models.package import PackageVersion
from models.planning import OperationAction as Action
from models.planning import utc_now
from models.platform import (
    Architecture,
    LinuxDistribution,
    LinuxFamily,
    OperatingSystem,
    Platform,
)
from package_managers.apt import APT
from package_managers.base import AdapterError
from package_managers.homebrew import Homebrew
from package_managers.inspection import APTInspector, HomebrewInspector
from services.catalog.loader import load_catalog
from services.catalog.service import CatalogService
from services.operations import OperationService
from services.planning import Planner
from services.records import PackageRequest, PlanConsent
from system.commands import CommandResult
from system.runner import CommandRunner


class InspectionTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

        def find(name):
            if not (self.root / name).exists():
                (self.root / name).write_bytes(
                    b"Synthetic reviewed executable; never executed."
                )
            return str(self.root / name)

        self.macos = Platform(OperatingSystem.MACOS, Architecture.ARM64)
        self.linux = Platform(
            OperatingSystem.LINUX,
            Architecture.X86_64,
            LinuxDistribution("ubuntu", LinuxFamily.DEBIAN, version="24.04"),
        )
        self.brew = Homebrew(self.macos, find=find)
        self.apt = APT(self.linux, find=find)
        self.catalog = CatalogService(load_catalog())
        self.runner = mock.create_autospec(CommandRunner, instance=True)
        self.runner.run.side_effect = self.run_read
        self.brew_reader = HomebrewInspector(self.brew, self.runner)
        self.key = self.root / "archive.gpg"
        self.key.write_bytes(b"Synthetic public key; never used for verification.")
        self.source = self.root / "ubuntu.sources"
        self.source.write_text(
            f"Types: deb\nURIs: http://archive.ubuntu.com/ubuntu\nSuites: noble\nComponents: main\nSigned-By: {self.key}\n"
        )
        self.cache = self.root / "lists"
        self.cache.mkdir()
        self.index = (
            self.cache
            / "archive.ubuntu.com_ubuntu_dists_noble_main_binary-amd64_Packages.lz4"
        )
        self.release = self.cache / "archive.ubuntu.com_ubuntu_dists_noble_InRelease"
        self.index.write_bytes(b"Synthetic native package index\n")
        self.helper = Path(find("apt-helper"))
        index_data = self.index.read_bytes()
        self.release.write_bytes(
            b"-----BEGIN PGP SIGNED MESSAGE-----\nHash: SHA512\n\nOrigin: Ubuntu\nCodename: noble\nSHA256:\n"
            + f" {hashlib.sha256(index_data).hexdigest()} {len(index_data)} main/binary-amd64/Packages\n".encode()
            + b"-----BEGIN PGP SIGNATURE-----\nSynthetic signature\n-----END PGP SIGNATURE-----\n"
        )
        self.gpg_status = (
            "[GNUPG:] VALIDSIG " + "A" * 40 + " 2026-01-01 1767225600 0 4 0 1 10 01\n"
        )
        self.apt_reader = APTInspector(
            self.apt,
            self.runner,
            source_roots=(self.root,),
            key_roots=(self.root,),
            list_roots=(self.cache,),
            helper_path=self.helper,
        )
        self.formula = {
            "name": "git",
            "tap": "homebrew/core",
            "versions": {"stable": "2.50.0"},
            "revision": 0,
            "dependencies": ["gettext", "pcre2"],
            "installed": [{"version": "2.49.0"}],
        }
        self.cask = {
            "token": "visual-studio-code",
            "tap": "homebrew/cask",
            "version": "1.100.0",
            "installed": "1.99.0",
            "depends_on": {"macos": {">=": ["12"]}},
        }
        self.brew_inventory = {"formulae": [self.formula], "casks": [self.cask]}
        self.brew_metadata = {"formulae": [self.formula], "casks": []}
        self.dpkg = "git\t1:2.43.0-1ubuntu7\tinstalled\nold-config\t1.0\tconfig-files\n"
        self.policy = """git:
  Installed: 1:2.43.0-1ubuntu7
  Candidate: 1:2.43.0-1ubuntu8
  Version table:
     1:2.43.0-1ubuntu8 990
        990 http://archive.ubuntu.com/ubuntu noble/main amd64 Packages
 *** 1:2.43.0-1ubuntu7 100
        100 /var/lib/dpkg/status
"""
        self.origins = """Package files:
 100 /var/lib/dpkg/status
     release a=now
 990 http://archive.ubuntu.com/ubuntu noble/main amd64 Packages
     release v=24.04,o=Ubuntu,a=noble,n=noble,l=Ubuntu,c=main,b=amd64
     origin archive.ubuntu.com
"""
        self.metadata = "Package: git\nVersion: 1:2.43.0-1ubuntu8\nArchitecture: amd64\nDescription: Git\n"
        self.simulation = "Reading package lists...\nInst git [1:2.43.0-1ubuntu7] (1:2.43.0-1ubuntu8 Ubuntu:24.04/noble [amd64])\nConf git (1:2.43.0-1ubuntu8 Ubuntu:24.04/noble [amd64])\n"
        self.truncated = False
        self.exit_code = 0
        self.error = None
        self.raw_json = None

    def spec(self, backend, package="git"):
        return next(
            s for s in self.catalog.get(package).install_specs if s.backend == backend
        )

    def result(self, command, stdout):
        now = utc_now()
        return CommandResult(
            command.preview(),
            now,
            now,
            self.exit_code,
            stdout,
            "",
            self.truncated,
            self.error,
        )

    def run_read(self, command, **kwargs):
        self.assertFalse(command.requires_consent)
        self.assertNotIn("approval", kwargs)
        argv = command.argv
        if Path(argv[0]).name == "brew":
            data = self.brew_inventory if "--installed" in argv else self.brew_metadata
            output = self.raw_json if self.raw_json is not None else json.dumps(data)
        elif Path(argv[0]).name == "dpkg-query":
            output = self.dpkg
        elif Path(argv[0]).name == "gpgv":
            output = self.gpg_status
        elif Path(argv[0]).name == "apt-helper":
            data = Path(argv[-1]).read_bytes()
            return replace(
                self.result(command, ""),
                stdout_sha256=hashlib.sha256(data).hexdigest(),
                stdout_size=len(data),
            )
        elif "indextargets" in argv:
            output = (
                f"{self.index}|main/binary-amd64/Packages|{self.source}:1\n"
                if "$(FILENAME)|$(METAKEY)|$(SOURCESENTRY)" in argv
                else f"{self.source}:1\n"
            )
        elif "--simulate" in argv:
            self.assertNotIn("pkexec", argv)
            self.assertIn("--assume-no", argv)
            output = self.simulation
        elif "show" in argv:
            output = self.metadata
        elif argv[-1] == "policy":
            output = self.origins
        else:
            self.assertIn("APT::Default-Release=noble", argv)
            output = self.policy
        return self.result(command, output)

    def test_homebrew_machine_inventory_retains_versions_and_sources(self):
        inventory = self.brew_reader.inventory()
        self.assertEqual(inventory.get("git").versions, ("2.49.0",))
        self.assertEqual(inventory.get("visual-studio-code").source_id, "homebrew-cask")
        command = self.runner.run.call_args.args[0]
        self.assertEqual(command.argv[1:], ("info", "--json=v2", "--installed"))
        self.assertIn(("HOMEBREW_NO_AUTO_UPDATE", "1"), command.environment)

    def test_homebrew_inventory_does_not_claim_unknown_tap_origin(self):
        self.formula["tap"] = "third-party/tools"
        self.assertEqual(
            self.brew_reader.inventory().get("git").source_id, "unreviewed-homebrew-tap"
        )

    def test_homebrew_formula_revision_and_metadata_stability(self):
        self.formula["revision"] = 2
        spec = self.spec("homebrew")
        first = self.brew_reader.review(spec, Action.UPGRADE)
        self.assertEqual(first.version, "2.50.0_2")
        self.formula["installed"] = [{"version": "2.50.0_2"}]
        self.formula["outdated"] = False
        self.assertEqual(first, self.brew_reader.review(spec, Action.UPGRADE))
        self.formula["dependencies"].append("new-dependency")
        self.assertNotEqual(
            first.fingerprint, self.brew_reader.review(spec, Action.UPGRADE).fingerprint
        )
        self.assertIn("not an exact transaction simulation", " ".join(first.changes))

    def test_homebrew_cask_review_and_removal_do_not_claim_service_health(self):
        self.brew_metadata = {"formulae": [], "casks": [self.cask]}
        spec = self.spec("homebrew", "vscode")
        review = self.brew_reader.review(spec, Action.INSTALL)
        self.assertEqual(review.version, "1.100.0")
        self.assertIn("macos", " ".join(review.changes))
        self.assertIsNone(self.brew_reader.review(spec, Action.UNINSTALL).version)

    def test_homebrew_unexpected_identity_tap_and_disabled_fail_closed(self):
        for key, value in (
            ("name", "different"),
            ("tap", "untrusted/tap"),
            ("disabled", True),
            ("revision", -1),
        ):
            with self.subTest(key=key):
                original = dict(self.formula)
                self.formula[key] = value
                with self.assertRaises(AdapterError):
                    self.brew_reader.review(self.spec("homebrew"), Action.INSTALL)
                self.formula.clear()
                self.formula.update(original)

    def test_malformed_duplicate_missing_or_truncated_native_json_is_not_absence(self):
        for raw in (
            "",
            "not json",
            "[]",
            '{"formulae": [], "formulae": [], "casks": []}',
            "{}",
        ):
            self.raw_json = raw
            with self.assertRaises(AdapterError):
                self.brew_reader.inventory()
        self.raw_json = None
        self.truncated = True
        with self.assertRaises(AdapterError):
            self.brew_reader.inventory()

    def test_apt_inventory_normalizes_only_matching_architecture(self):
        self.dpkg += "zlib1g:amd64\t1.3\tinstalled\nzlib1g:arm64\t1.3\tinstalled\n"
        inventory = self.apt_reader.inventory()
        self.assertEqual(inventory.get("zlib1g").versions, ("1.3",))
        self.assertEqual(inventory.get("zlib1g:amd64"), inventory.get("zlib1g"))
        self.assertEqual(inventory.get("git:amd64"), inventory.get("git"))
        self.assertIsNotNone(inventory.get("zlib1g:arm64"))
        self.assertIsNone(inventory.get("old-config"))
        self.assertIsNone(inventory.get("git").source_id)

    def test_apt_inventory_partial_malformed_and_duplicate_rows_fail_closed(self):
        for output in (
            "git\t1\thalf-configured\n",
            "git 1 installed\n",
            "git\t1\tinstalled\ngit\t2\tinstalled\n",
        ):
            self.dpkg = output
            with self.assertRaises(AdapterError):
                self.apt_reader.inventory()

    def test_apt_review_pins_native_candidate_and_previews_simulated_effects(self):
        spec = self.spec("apt")
        review = self.apt_reader.review(spec, Action.UPGRADE)
        self.assertEqual(review.version, "1:2.43.0-1ubuntu8")
        self.assertEqual(len(review.changes), 2)
        commands = [call.args[0] for call in self.runner.run.call_args_list]
        self.assertTrue(all(not c.mutates and not c.requires_consent for c in commands))
        simulation = next(
            command for command in commands if "--simulate" in command.argv
        )
        self.assertEqual(
            simulation.argv[1:],
            (
                "--simulate",
                "--assume-no",
                "--target-release",
                "noble",
                "install",
                "--only-upgrade",
                "--",
                "git=1:2.43.0-1ubuntu8",
            ),
        )
        self.assertTrue(all(c.environment == (("LC_ALL", "C"),) for c in commands))

    def test_changed_key_blocks_reviewed_upgrade_before_dispatch(self):
        planner = Planner(self.catalog, self.linux, (self.apt_reader,))
        reviewed = planner.preview((PackageRequest("git", Action.UPGRADE),))
        self.assertTrue(reviewed.plan.ready)
        self.key.write_bytes(b"Changed public key; native cache output stays the same.")
        report = OperationService((self.apt_reader,), self.runner).execute(
            reviewed, consent=PlanConsent(reviewed, True, True)
        )
        result = report.operations[0].result
        self.assertEqual(result.error.code, ErrorCode.VERIFICATION_FAILED)
        self.assertIsNone(result.exit_code)
        self.assertTrue(
            all(not call.args[0].mutates for call in self.runner.run.call_args_list)
        )

    def test_key_changed_during_native_review_is_rejected(self):
        original = self.runner.run.side_effect

        def change(command, **kwargs):
            if "show" in command.argv:
                self.key.write_bytes(b"Changed during review")
            return original(command, **kwargs)

        self.runner.run.side_effect = change
        with self.assertRaises(AdapterError):
            self.apt_reader.review(self.spec("apt"), Action.INSTALL)

    def test_bad_cached_release_blocks_new_and_stale_plans_before_mutation(self):
        planner = Planner(self.catalog, self.linux, (self.apt_reader,))
        reviewed = planner.preview((PackageRequest("git", Action.UPGRADE),))
        self.assertTrue(reviewed.plan.ready)
        self.gpg_status = "[GNUPG:] BADSIG synthetic\n"
        self.assertFalse(
            planner.preview((PackageRequest("git", Action.UPGRADE),)).plan.ready
        )
        report = OperationService((self.apt_reader,), self.runner).execute(
            reviewed, consent=PlanConsent(reviewed, True, True)
        )
        self.assertEqual(
            report.operations[0].result.error.code, ErrorCode.VERIFICATION_FAILED
        )
        self.assertIsNone(report.operations[0].result.exit_code)
        self.assertTrue(
            all(not call.args[0].mutates for call in self.runner.run.call_args_list)
        )

    def test_missing_gpgv_still_allows_inventory_and_removal_review(self):
        finder = self.apt.find
        self.apt.find = lambda name: None if name == "gpgv" else finder(name)
        self.assertIsNotNone(self.apt_reader.inventory().get("git"))
        self.assertIsNone(
            self.apt_reader.review(self.spec("apt"), Action.UNINSTALL).version
        )
        with self.assertRaises(AdapterError) as caught:
            self.apt_reader.review(self.spec("apt"), Action.INSTALL)
        self.assertEqual(caught.exception.error.code, ErrorCode.MISSING_BACKEND)

    def test_package_index_corruption_blocks_new_and_stale_plans_and_restores(self):
        planner = Planner(self.catalog, self.linux, (self.apt_reader,))
        reviewed = planner.preview((PackageRequest("git", Action.UPGRADE),))
        self.assertTrue(reviewed.plan.ready)
        original = self.index.read_bytes()
        self.index.write_bytes(b"Corrupted cached package index")
        self.assertFalse(
            planner.preview((PackageRequest("git", Action.UPGRADE),)).plan.ready
        )
        report = OperationService((self.apt_reader,), self.runner).execute(
            reviewed, consent=PlanConsent(reviewed, True, True)
        )
        self.assertEqual(
            report.operations[0].result.error.code, ErrorCode.VERIFICATION_FAILED
        )
        self.assertIsNone(report.operations[0].result.exit_code)
        self.assertTrue(
            all(not call.args[0].mutates for call in self.runner.run.call_args_list)
        )
        self.index.write_bytes(original)
        self.assertEqual(
            planner.preview((PackageRequest("git", Action.UPGRADE),))
            .prepared[0]
            .review,
            reviewed.prepared[0].review,
        )

    def test_apt_unreviewed_origin_and_ambiguous_candidates_are_rejected(self):
        original = self.origins
        self.origins = self.origins.replace("o=Ubuntu", "o=Untrusted")
        with self.assertRaises(AdapterError):
            self.apt_reader.review(self.spec("apt"), Action.INSTALL)
        self.origins = original
        self.policy = self.policy.replace(
            "Candidate: 1:2.43.0-1ubuntu8", "Candidate: (none)"
        )
        with self.assertRaises(AdapterError):
            self.apt_reader.review(self.spec("apt"), Action.INSTALL)

    def test_apt_same_version_available_from_untrusted_origin_is_rejected(self):
        self.policy = self.policy.replace(
            "990 http://archive.ubuntu.com/ubuntu noble/main amd64 Packages",
            "990 http://archive.ubuntu.com/ubuntu noble/main amd64 Packages\n        990 https://untrusted.invalid/ubuntu noble/main amd64 Packages",
        )
        with self.assertRaises(AdapterError):
            self.apt_reader.review(self.spec("apt"), Action.INSTALL)

    def test_apt_requested_version_must_have_matching_metadata_and_origin(self):
        spec = replace(self.spec("apt"), version=PackageVersion("0.9"))
        with self.assertRaises(AdapterError):
            self.apt_reader.review(spec, Action.INSTALL)
        self.metadata = self.metadata.replace("Package: git", "Package: different")
        with self.assertRaises(AdapterError):
            self.apt_reader.review(self.spec("apt"), Action.INSTALL)

    def test_apt_removal_preview_includes_native_reverse_removals(self):
        self.simulation = "Remv git [1:2.43.0]\nRemv dependent [1.0]\n"
        review = self.apt_reader.review(self.spec("apt"), Action.UNINSTALL)
        self.assertIsNone(review.version)
        self.assertEqual(
            review.changes, ("Remv git [1:2.43.0]", "Remv dependent [1.0]")
        )
        self.assertEqual(self.runner.run.call_count, 1)

    def test_apt_purge_and_simulation_errors_are_not_executable_plans(self):
        for text in ("Purg git [1]\n", "E: Failed transaction\n", "Inst --option\n"):
            self.simulation = text
            with self.assertRaises(AdapterError):
                self.apt_reader.review(self.spec("apt"), Action.INSTALL)

    def test_native_process_failure_timeout_and_cancellation_propagate(self):
        for code in (ErrorCode.NETWORK, ErrorCode.TIMEOUT, ErrorCode.CANCELLED):
            self.error = OperationError(code, "Native inspection failed.")
            with self.assertRaises(AdapterError) as caught:
                self.apt_reader.inventory()
            self.assertEqual(caught.exception.error.code, code)
        self.error = None
        self.exit_code = 2
        with self.assertRaises(AdapterError):
            self.apt_reader.inventory()

    def test_apt_end_to_end_review_approval_exact_command_and_inventory_verification(
        self,
    ):
        planner = Planner(self.catalog, self.linux, (self.apt_reader,))
        reviewed = planner.preview((PackageRequest("git", Action.UPGRADE),))
        self.assertTrue(reviewed.plan.ready, reviewed.plan.issues)
        self.assertEqual(reviewed.plan.items[0].spec.version.value, "1:2.43.0-1ubuntu8")
        mutations = []

        def run(command, **kwargs):
            if command.mutates:
                mutations.append(command)
                self.assertTrue(kwargs["approval"].permits(command))
                self.assertEqual(command.argv[-1], "git=1:2.43.0-1ubuntu8")
                self.dpkg = "git\t1:2.43.0-1ubuntu8\tinstalled\n"
                return self.result(command, "native completed")
            return self.run_read(command, **kwargs)

        self.runner.run.side_effect = run
        report = OperationService((self.apt_reader,), self.runner).execute(
            reviewed, consent=PlanConsent(reviewed, True, True)
        )
        self.assertEqual(report.operations[0].state.value, "succeeded", report)
        self.assertEqual(len(mutations), 1)

    def test_apt_up_to_date_upgrade_is_read_only_skip_even_without_effects(self):
        self.dpkg = "git\t1:2.43.0-1ubuntu8\tinstalled\n"
        self.simulation = "0 upgraded, 0 newly installed, 0 to remove\n"
        plan = Planner(self.catalog, self.linux, (self.apt_reader,)).preview(
            (PackageRequest("git", Action.UPGRADE),)
        )
        self.assertTrue(plan.plan.ready)
        self.assertIsNotNone(plan.plan.items[0].skip_reason)
