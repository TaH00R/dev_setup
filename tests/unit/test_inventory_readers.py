"""Cross-platform inventory contracts; no package installations or live registries."""

import json
import tempfile
import types
import unittest
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest import mock

from app.bootstrap import create_services
from models.errors import ErrorCode, OperationError
from models.inventory import InstalledPackage, Inventory
from models.planning import utc_now
from models.platform import Architecture, OperatingSystem, Platform
from package_managers.base import AdapterError
from package_managers.inventory import ReadOnlyInventory, parse_json
from package_managers.registry import adapters_for
from services.catalog.loader import load_catalog
from services.desktop import DesktopServices
from services.inventory import InventoryService, InventorySource
from system.commands import CommandResult
from system.platform import distribution_from_release
from system.runner import CommandRunner
from system.software import windows_applications


def host(os_name="linux", distro="arch"):
    return Platform(
        OperatingSystem(os_name),
        Architecture.X86_64,
        distribution_from_release({"ID": distro, "VERSION_ID": "43"})
        if os_name == "linux"
        else None,
    )


class InventoryReaderTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        for name in (
            "winget.exe",
            "scoop.ps1",
            "powershell.exe",
            "rpm",
            "pacman",
            "dnf",
            "zypper",
            "apk",
            "flatpak",
            "brew",
            "apt-get",
            "apt-cache",
            "dpkg-query",
            "dpkg",
        ):
            (self.root / name).touch()
        self.runner = mock.create_autospec(CommandRunner, instance=True)

    def adapter(self, target, backend):
        def find(name):
            path = self.root / name
            return str(path) if path.exists() else None

        return next(a for a in adapters_for(target, find=find) if a.id == backend)

    def result(self, output="", **kwargs):
        return CommandResult(mock.Mock(), utc_now(), utc_now(), 0, output, **kwargs)

    def test_each_backend_has_read_only_inventory_and_exact_native_arguments(self):
        cases = [
            (
                "arch",
                "pacman",
                "git 2.50.0-1\nlibc++ 20.1-1\n",
                ("pacman", "-Q"),
                "git",
            ),
            (
                "fedora",
                "dnf",
                "git\t0:2.50.0-1.fc43\tx86_64\n",
                (
                    "rpm",
                    "-qa",
                    "--queryformat",
                    r"%{NAME}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}\t%{ARCH}\n",
                ),
                "git.x86_64",
            ),
            (
                "opensuse-tumbleweed",
                "zypper",
                "git\t0:2.50.0-1\tx86_64\n",
                (
                    "rpm",
                    "-qa",
                    "--queryformat",
                    r"%{NAME}\t%{EPOCHNUM}:%{VERSION}-%{RELEASE}\t%{ARCH}\n",
                ),
                "git.x86_64",
            ),
            (
                "alpine",
                "apk",
                "git-2.50.0-r0\nlibstdc++-14.2.0-r6\n.redis-rundeps-20261026.164239\n",
                ("apk", "--no-network", "info", "--verbose"),
                "git",
            ),
            (
                "arch",
                "flatpak",
                "app/org.example.App/x86_64/stable\t1.0\tcustom\tuser\napp/org.example.App/x86_64/beta\t\tflathub\tsystem\n",
                (
                    "flatpak",
                    "list",
                    "--app",
                    "--columns=ref:full,version:full,origin:full,installation:full",
                ),
                "app/org.example.App/x86_64/stable [user; custom]",
            ),
        ]
        for distro, backend, output, args, identity in cases:
            with self.subTest(backend=backend):
                adapter = self.adapter(host(distro=distro), backend)
                self.runner.run.return_value = self.result(output)
                inventory = ReadOnlyInventory(adapter, self.runner).inventory()
                self.assertIsNotNone(inventory.get(identity))
                command = self.runner.run.call_args.args[0]
                self.assertEqual(command.argv, (str(self.root / args[0]), *args[1:]))
                self.assertFalse(command.requires_consent)
                self.assertTrue(all(p.source_id is None for p in inventory.packages))

    def test_scoop_export_preserves_custom_bucket_and_scope_without_source_trust(self):
        adapter = self.adapter(host("windows"), "scoop")
        self.runner.run.return_value = self.result(
            json.dumps(
                {
                    "apps": [
                        {
                            "Name": "git",
                            "Version": "2.50",
                            "Source": "custom",
                            "Info": "",
                        },
                        {
                            "Name": "git",
                            "Version": "2.49",
                            "Source": "main",
                            "Info": "Global install",
                        },
                    ]
                }
            )
        )
        inventory = ReadOnlyInventory(adapter, self.runner).inventory()
        self.assertEqual(len(inventory.packages), 2)
        self.assertIsNotNone(inventory.get("custom/git [user]"))
        command = self.runner.run.call_args.args[0]
        self.assertIn("inventory-json", command.argv)
        self.assertIn("-File", command.argv)
        self.assertNotIn("-Command", command.argv)
        self.assertFalse(command.requires_consent)

    def test_winget_uses_temporary_json_and_keeps_unknown_versions_unknown(self):
        adapter = self.adapter(host("windows"), "winget")
        outputs = []

        def run(command, **kwargs):
            output = Path(command.argv[command.argv.index("--output") + 1])
            outputs.append(output)
            output.write_text(
                json.dumps(
                    {
                        "Sources": [
                            {
                                "Packages": [
                                    {"PackageIdentifier": "Git.Git", "Version": "2.50"},
                                    {"PackageIdentifier": "Other.Tool"},
                                ]
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(command.argv[1], "export")
            self.assertIn("--disable-interactivity", command.argv)
            self.assertNotIn("--accept-source-agreements", command.argv)
            self.assertFalse(command.requires_consent)
            return self.result()

        self.runner.run.side_effect = run
        inventory = ReadOnlyInventory(adapter, self.runner).inventory()
        self.assertEqual(inventory.get("Git.Git").versions, ("2.50",))
        self.assertEqual(inventory.get("Other.Tool").versions, ())
        self.assertFalse(outputs[0].exists())

    def test_failed_truncated_malformed_and_cancelled_reads_are_not_empty_inventory(
        self,
    ):
        adapter = self.adapter(host(distro="arch"), "pacman")
        cases = [
            self.result("bad unsupported header"),
            self.result("git 1\n", output_truncated=True),
            replace(self.result(), exit_code=1),
            self.result(error=OperationError(ErrorCode.TIMEOUT, "Timed out.")),
        ]
        for result in cases:
            self.runner.run.return_value = result
            with self.assertRaises(AdapterError):
                ReadOnlyInventory(adapter, self.runner).inventory()
        event = Event()
        event.set()
        self.runner.reset_mock()
        with self.assertRaises(AdapterError) as error:
            ReadOnlyInventory(adapter, self.runner).inventory(event)
        self.assertEqual(error.exception.error.code, ErrorCode.CANCELLED)
        self.runner.run.assert_not_called()
        for output in ('{"apps":[],"apps":[]}', "[]", "garbage"):
            with self.assertRaises(AdapterError):
                parse_json(output)

    def test_all_platforms_register_native_sources_without_mutation_inspectors(self):
        for target, expected in [
            (host("windows"), {"winget", "scoop", "windows-registry"}),
            (host("macos"), {"homebrew"}),
            *[
                (host(distro=d), {backend, "flatpak"})
                for d, backend in [
                    ("ubuntu", "apt"),
                    ("arch", "pacman"),
                    ("fedora", "dnf"),
                    ("opensuse-tumbleweed", "zypper"),
                    ("alpine", "apk"),
                ]
            ],
        ]:
            with (
                self.subTest(target=target),
                mock.patch("app.bootstrap.detect_platform", return_value=target),
            ):
                services = create_services()
                self.assertEqual(
                    {s.backend for s in services.inventory.sources}, expected
                )
                if target.os == OperatingSystem.WINDOWS:
                    self.assertEqual(services.inspectors, ())

    def test_detection_runs_without_any_inventory_reader(self):
        packages = load_catalog()
        # Catalog loader returns a catalog model, handled below via the service.
        from services.catalog.service import CatalogService

        catalog = CatalogService(packages)
        service = InventoryService((), self.runner, host=host("windows"))
        self.runner.run.return_value = self.result(stderr="git version 2.50")
        with mock.patch(
            "services.inventory.shutil.which",
            side_effect=lambda name: (
                str(self.root / "git.exe") if name == "git" else None
            ),
        ):
            report = service.collect((catalog.get("git"),))
        self.assertTrue(report.detections[0].present)
        self.assertEqual(report.detections[0].output, "git version 2.50")
        self.assertFalse(report.snapshots)
        self.assertFalse(self.runner.run.call_args.args[0].requires_consent)

    def test_source_failure_does_not_hide_other_sources_or_path_observations(self):
        from services.catalog.service import CatalogService

        catalog = CatalogService(load_catalog())

        def failure(cancel):
            raise AdapterError(ErrorCode.MISSING_BACKEND, "Missing manager.")

        services = DesktopServices(
            catalog,
            host("windows"),
            (),
            (),
            self.runner,
            inventory_sources=(
                InventorySource("winget", failure),
                InventorySource(
                    "example",
                    lambda cancel: Inventory(
                        "example", (InstalledPackage("custom", ("1",)),)
                    ),
                ),
            ),
        )
        self.runner.run.return_value = self.result("git version 2.50")
        with mock.patch(
            "services.inventory.shutil.which",
            side_effect=lambda name: (
                str(self.root / "git.exe") if name == "git" else None
            ),
        ):
            snapshot = services.refresh(Event())
        self.assertTrue(snapshot.inventory.errors)
        self.assertEqual(
            {row.backend for row in snapshot.installed}, {"example", "executable"}
        )
        self.assertIsNone(
            next(
                row for row in snapshot.installed if row.backend == "executable"
            ).mapping
        )
        self.assertFalse(services.inspectors)

    def test_windows_registration_reads_machine_user_and_both_architecture_views(self):
        class Key:
            def __init__(self, location, entry=None):
                self.location = location
                self.entry = entry

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        entries = {
            "app": {"DisplayName": "Example\x00 Tool", "DisplayVersion": "1.2"},
            "internal": {},
        }
        opened = []

        def open_key(root, path, reserved=0, access=0):
            if isinstance(root, Key):
                return Key(root.location, path)
            opened.append((root, access))
            return Key((root, access))

        def query(key, name):
            if name not in entries[key.entry]:
                raise FileNotFoundError()
            return entries[key.entry][name], 1

        registry = types.SimpleNamespace(
            HKEY_LOCAL_MACHINE=1,
            HKEY_CURRENT_USER=2,
            KEY_READ=4,
            KEY_WOW64_64KEY=64,
            KEY_WOW64_32KEY=32,
            OpenKey=open_key,
            QueryInfoKey=lambda key: (2, 0, 0),
            EnumKey=lambda key, index: tuple(entries)[index],
            QueryValueEx=query,
        )
        with (
            mock.patch.dict("sys.modules", {"winreg": registry}),
            mock.patch("system.software.sys.platform", "win32"),
        ):
            inventory = windows_applications()
            event = Event()
            event.set()
            with self.assertRaises(AdapterError) as error:
                windows_applications(event)
            self.assertEqual(error.exception.error.code, ErrorCode.CANCELLED)
        self.assertEqual(len(inventory.packages), 3)  # Shared HKCU views deduplicate.
        self.assertTrue(all(p.versions == ("1.2",) for p in inventory.packages))
        self.assertTrue(
            all(p.native_id.startswith("Example Tool") for p in inventory.packages)
        )
        self.assertTrue({(1, 68), (1, 36), (2, 68), (2, 36)}.issubset(opened))

    def test_partial_native_inventory_is_rejected_and_detection_survives(self):
        service = InventoryService(
            (),
            self.runner,
            host=host("windows"),
            sources=(
                InventorySource("expected", lambda cancel: Inventory("wrong", ())),
            ),
        )
        with mock.patch("services.inventory.shutil.which", return_value=None):
            report = service.collect()
        self.assertFalse(report.snapshots)
        self.assertEqual(report.errors[0][1].code, ErrorCode.VERIFICATION_FAILED)
