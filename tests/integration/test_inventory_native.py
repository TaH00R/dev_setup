"""Opt-in read-only native checks on CI hosts and disposable Linux images."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest import mock

from app.bootstrap import create_services
from models.planning import utc_now
from models.platform import Architecture, OperatingSystem, Platform
from package_managers.inspection import APTInspector
from package_managers.inventory import ReadOnlyInventory
from package_managers.registry import adapters_for
from system.commands import CommandResult
from system.platform import distribution_from_release
from system.runner import CommandRunner


class NativeInventoryTests(unittest.TestCase):
    @unittest.skipUnless(
        os.environ.get("DEV_SETUP_NATIVE_INVENTORY") == "1",
        "Native inventory is opt-in.",
    )
    def test_host_read_only_inventory(self):
        services = create_services()
        backend = {
            OperatingSystem.WINDOWS: "windows-registry",
            OperatingSystem.MACOS: "homebrew",
            OperatingSystem.LINUX: "apt",
        }[services.host.os]
        source = next(s for s in services.inventory.sources if s.backend == backend)
        inventory = source.read(Event())
        self.assertEqual(inventory.backend, backend)
        self.assertTrue(inventory.packages)
        print(f"Native {backend} inventory: {len(inventory.packages)} records")

    @unittest.skipUnless(
        os.environ.get("DEV_SETUP_INVENTORY_IMAGE"),
        "Disposable Linux image check is opt-in.",
    )
    def test_linux_image_read_only_inventory(self):
        image = os.environ["DEV_SETUP_INVENTORY_IMAGE"]
        distro = os.environ["DEV_SETUP_INVENTORY_DISTRO"]
        backend = os.environ["DEV_SETUP_INVENTORY_BACKEND"]
        target = Platform(
            OperatingSystem.LINUX,
            Architecture.X86_64,
            distribution_from_release({"ID": distro}),
        )
        with tempfile.TemporaryDirectory() as directory:
            # Resolve synthetic host paths, then dispatch the exact adapter-owned
            # argument list to its native executable inside a disposable container.
            adapter = next(
                a
                for a in adapters_for(
                    target, find=lambda name: str(Path(directory) / name)
                )
                if a.id == backend
            )
            runner = mock.create_autospec(CommandRunner, instance=True)

            def run(command, **kwargs):
                started = utc_now()
                result = subprocess.run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--network",
                        "none",
                        "--read-only",
                        "--cap-drop",
                        "ALL",
                        "--security-opt",
                        "no-new-privileges",
                        "--entrypoint",
                        Path(command.argv[0]).name,
                        image,
                        *command.argv[1:],
                    ],
                    shell=False,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=180,
                )
                return CommandResult(
                    command.preview(),
                    started,
                    utc_now(),
                    result.returncode,
                    result.stdout,
                    result.stderr,
                )

            runner.run.side_effect = run
            reader = (
                APTInspector(adapter, runner)
                if backend == "apt"
                else ReadOnlyInventory(adapter, runner)
            )
            inventory = reader.inventory(Event())
            self.assertTrue(inventory.packages)
            self.assertTrue(
                all(
                    not call.args[0].requires_consent
                    for call in runner.run.call_args_list
                )
            )
            print(
                f"Native {backend} inventory in {image}: {len(inventory.packages)} records"
            )
