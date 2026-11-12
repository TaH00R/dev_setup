"""Native search fixtures: no live registry or package manager is invoked."""

import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from models.errors import ErrorCode, OperationError
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
from package_managers.discovery import NativeSearchProvider, clean_text, providers_for
from package_managers.flatpak import Flatpak
from package_managers.homebrew import Homebrew
from package_managers.linux import APK, DNF, Pacman, Zypper
from package_managers.scoop import Scoop
from package_managers.winget import WinGet
from system.commands import CommandResult
from system.runner import CommandRunner


class SearchProviderTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "scoop.ps1").write_text("# fixture only", encoding="utf-8")
        self.find = lambda name: str(self.root / name)
        self.runner = mock.create_autospec(CommandRunner, instance=True)
        self.mac = Platform(OperatingSystem.MACOS, Architecture.ARM64)
        self.windows = Platform(OperatingSystem.WINDOWS, Architecture.X86_64)

    def linux(self, distro="ubuntu", family=LinuxFamily.DEBIAN, version="24.04"):
        return Platform(
            OperatingSystem.LINUX,
            Architecture.X86_64,
            LinuxDistribution(distro, family, version=version),
        )

    def provider(self, adapter, **kwargs):
        return NativeSearchProvider(adapter, self.runner, **kwargs)

    def test_homebrew_sources_and_taps_are_preserved(self):
        adapter = Homebrew(self.mac, find=self.find)
        formula = self.provider(adapter, brew_kind="formula")
        cask = self.provider(adapter, brew_kind="cask")
        rows = formula.parse("==> Formulae\ngit  custom/tools/other  git-helper\n")
        self.assertEqual(rows.packages[0].source_id, "homebrew-core")
        self.assertEqual(rows.packages[1].source_id, "homebrew-tap:custom/tools")
        self.assertEqual(rows.packages[1].native_id, "custom/tools/other")
        self.assertEqual(
            cask.parse("==> Casks\nvisual-studio-code\n").packages[0].source_id,
            "homebrew-cask",
        )
        command = formula.command("git", 2)
        self.assertEqual(command.argv[1:], ("search", "--formula", "--", "git"))
        self.assertIn(("HOMEBREW_NO_AUTO_UPDATE", "1"), command.environment)
        self.assertEqual(command.timeout_seconds, 2)
        with self.assertRaises(AdapterError):
            formula.parse("==> Casks\nnot-a-formula\n")

    def test_apt_cache_source_is_not_misrepresented_as_reviewed_ubuntu_origin(self):
        provider = self.provider(APT(self.linux(), find=self.find))
        rows = provider.parse(
            "git - Distributed version control\nlibgit2:amd64 - Library\n"
        )
        self.assertEqual(rows.packages[0].source_id, "apt-configured")
        self.assertEqual(rows.packages[1].native_id, "libgit2:amd64")
        self.assertIn("origin is unknown", " ".join(rows.packages[0].caveats))
        self.assertFalse(provider.command("git", 1).requires_consent)

    def test_pacman_retains_repository_and_multiline_description(self):
        provider = self.provider(
            Pacman(self.linux("arch", LinuxFamily.ARCH, None), find=self.find)
        )
        rows = provider.parse(
            "extra/git 2.50.0-1 [installed]\n    Distributed control\n    More description\ncustom/git 2.49-1\n    Other repository\n"
        )
        self.assertEqual(len(rows.packages), 2)
        self.assertEqual(
            rows.packages[0].summary, "Distributed control More description"
        )
        self.assertEqual(rows.packages[1].source_id, "pacman:custom")

    def test_dnf4_and_dnf5_formats_keep_architecture_qualified_ids(self):
        provider = self.provider(
            DNF(self.linux("fedora", LinuxFamily.FEDORA, "43"), find=self.find)
        )
        for output in (
            "======== Name Matched: git ========\ngit.x86_64 : Version control\n",
            "Matched fields: name (exact)\n git.x86_64\tVersion control\n",
        ):
            self.assertEqual(provider.parse(output).packages[0].native_id, "git.x86_64")
        self.assertIn("--cacheonly", provider.command("git", 2).argv)

    def test_apk_version_suffix_is_not_part_of_native_identifier(self):
        provider = self.provider(
            APK(self.linux("alpine", LinuxFamily.ALPINE, "3.22.0"), find=self.find)
        )
        rows = provider.parse("git-2.49.1-r0\nlibexample-1-2.0_rc1-r2\n")
        self.assertEqual(rows.packages[0].native_id, "git")
        self.assertEqual(rows.packages[1].native_id, "libexample-1")
        self.assertEqual(rows.packages[1].version, "2.0_rc1-r2")
        self.assertIn("--no-network", provider.command("git", 2).argv)

    def test_zypper_xml_keeps_repo_version_and_rejects_entity_declarations(self):
        provider = self.provider(
            Zypper(
                self.linux("opensuse-tumbleweed", LinuxFamily.SUSE, None),
                find=self.find,
            )
        )
        output = '<stream><search-result><solvable-list><solvable name="git-core" kind="package" edition="2.50-1" repository="repo-oss" arch="x86_64"/></solvable-list></search-result></stream>'
        row = provider.parse(output).packages[0]
        self.assertEqual(
            (row.native_id, row.source_id, row.version),
            ("git-core", "zypper:repo-oss", "2.50-1"),
        )
        self.assertIn("--xmlout", provider.command("git", 2).argv)
        with self.assertRaises(AdapterError):
            provider.parse('<!DOCTYPE stream [<!ENTITY x "danger">]>' + output)
        with self.assertRaises(AdapterError):
            provider.parse('<stream><message type="error">Failed</message></stream>')

    def test_flatpak_preserves_each_remote_and_requests_full_columns(self):
        provider = self.provider(Flatpak(self.linux(), find=self.find))
        rows = provider.parse(
            "org.example.Tool\tExample\tDeveloper tool\tflathub, custom\n"
        )
        self.assertEqual(len(rows.packages), 2)
        self.assertEqual(
            {p.source_id for p in rows.packages}, {"flathub", "flatpak-user:custom"}
        )
        self.assertTrue(
            any("application:full" in arg for arg in provider.command("tool", 2).argv)
        )
        self.assertIn("--user", provider.command("tool", 2).argv)
        self.assertFalse(provider.parse("No matches found\n").packages)

    def test_winget_fixed_columns_and_unknown_localized_formats_fail_closed(self):
        provider = self.provider(WinGet(self.windows, find=self.find))
        header = f"{'Name':24}{'Id':24}{'Version':12}Match"
        output = (
            header
            + "\n"
            + "-" * len(header)
            + "\n"
            + f"{'Example Tool':24}{'Example.Tool':24}{'2.0':12}Tag: example\n"
        )
        row = provider.parse(output).packages[0]
        self.assertEqual(
            (row.name, row.native_id, row.version),
            ("Example Tool", "Example.Tool", "2.0"),
        )
        self.assertIn("--count", provider.command("example", 2).argv)
        for invalid in (
            output.replace("Name", "Nom "),
            output.replace("Example.Tool", "Example.T…l"),
            output.replace("Example Tool", "例子 Tool"),
        ):
            with self.assertRaises(AdapterError):
                provider.parse(invalid)

    def test_scoop_json_is_only_requested_through_the_fixed_bridge(self):
        provider = self.provider(Scoop(self.windows, find=self.find))
        command = provider.command("git", 1)
        self.assertIn("-File", command.argv)
        self.assertIn("search-json", command.argv)
        self.assertNotIn("-Command", command.argv)
        rows = provider.parse(
            json.dumps(
                {
                    "packages": [{"Name": "git", "Source": "main", "Version": "2.50"}],
                    "limited": False,
                }
            )
        )
        self.assertEqual(rows.packages[0].source_id, "scoop-main")
        with self.assertRaises(AdapterError):
            provider.parse('{"packages":[],"packages":[],"limited":false}')

    def test_query_injection_is_rejected_before_process_execution(self):
        provider = self.provider(WinGet(self.windows, find=self.find))
        for query in ("--source bad", "git; calc", "git\ninstall", "$(cmd)"):
            with self.assertRaises(AdapterError):
                provider.search(query, threading.Event(), 1)
        self.runner.run.assert_not_called()

    def test_missing_manager_never_bootstraps_or_executes(self):
        provider = self.provider(
            Homebrew(self.mac, find=lambda name: None), brew_kind="formula"
        )
        with self.assertRaises(AdapterError) as caught:
            provider.search("git", threading.Event(), 1)
        self.assertEqual(caught.exception.error.code, ErrorCode.MISSING_BACKEND)
        self.runner.run.assert_not_called()

    def test_native_errors_timeouts_and_truncated_output_are_not_empty_successes(self):
        provider = self.provider(APT(self.linux(), find=self.find))
        command = provider.command("git", 1)
        for code in (ErrorCode.NETWORK, ErrorCode.TIMEOUT, ErrorCode.CANCELLED):
            now = utc_now()
            self.runner.run.return_value = CommandResult(
                command.preview(),
                now,
                now,
                1,
                error=OperationError(code, "Synthetic native search failure."),
            )
            with self.assertRaises(AdapterError) as caught:
                provider.search("git", threading.Event(), 1)
            self.assertEqual(caught.exception.error.code, code)
        self.runner.run.return_value = CommandResult(
            command.preview(), now, now, 0, "git - A tool", output_truncated=True
        )
        with self.assertRaises(AdapterError):
            provider.search("git", threading.Event(), 1)

    def test_untrusted_terminal_text_is_sanitized_and_never_installable(self):
        provider = self.provider(APT(self.linux(), find=self.find))
        row = provider.parse(
            "git - \x1b[31m<b>Literal markup</b>\x1b[0m \u202euntrusted\n"
        ).packages[0]
        self.assertIn("<b>Literal markup</b>", row.summary)
        self.assertNotIn("\x1b", row.summary)
        self.assertNotIn("\u202e", row.summary)
        self.assertFalse(row.installable)
        self.assertLessEqual(len(clean_text("x" * 2000)), 1024)

    def test_output_limits_and_unknown_formats_are_explicit(self):
        provider = self.provider(APT(self.linux(), find=self.find))
        result = provider.parse("\n".join(f"tool{i} - Tool" for i in range(201)))
        self.assertEqual(len(result.packages), 200)
        self.assertTrue(result.limited)
        for text in ("ambiguous native output", "x" * 65537):
            with self.assertRaises(AdapterError):
                provider.parse(text)

    def test_factory_is_lazy_and_splits_homebrew_scopes(self):
        rows = providers_for((Homebrew(self.mac, find=self.find),), self.runner)
        self.assertEqual([p.id for p in rows], ["homebrew-core", "homebrew-cask"])
        self.runner.run.assert_not_called()
