"""Mock-backed desktop services; never execute a native manager."""

import threading
from pathlib import Path
from unittest import mock

from models.errors import ErrorCode, OperationError
from models.inventory import InstalledPackage, Inventory, NativeReview
from models.planning import OperationAction, utc_now
from models.platform import Architecture, OperatingSystem, Platform
from package_managers.base import AdapterError
from package_managers.homebrew import Homebrew
from services.catalog.loader import load_catalog
from services.catalog.service import CatalogService
from services.desktop import DesktopServices
from system.commands import CommandResult, OutputSnapshot
from system.runner import CommandRunner


class DesktopFixture:
    def __init__(self, root: Path):
        self.adapter = Homebrew(
            Platform(OperatingSystem.MACOS, Architecture.ARM64),
            find=lambda name: str(root / name),
        )
        self.installed = {}
        self.threads = []
        self.mutations = []
        self.inventory_error = None
        self.failure = None
        self.mutate = True
        self.block = False
        self.entered = threading.Event()
        self.release = threading.Event()
        self.runner = mock.create_autospec(CommandRunner, instance=True)
        self.runner.run.side_effect = self.run
        self.services = DesktopServices(
            CatalogService(load_catalog()),
            self.adapter.host,
            (self.adapter,),
            (self,),
            self.runner,
        )

    def install(self, native_id="git", version="1.0", source="homebrew-core"):
        self.installed[native_id] = InstalledPackage(native_id, (version,), source)

    def inventory(self, cancel=None):
        self.threads.append(threading.get_ident())
        if self.inventory_error:
            raise AdapterError(self.inventory_error, "Synthetic inventory unavailable.")
        return Inventory("homebrew", tuple(self.installed.values()))

    def review(self, spec, action, cancel=None):
        self.threads.append(threading.get_ident())
        return NativeReview(
            None if action == OperationAction.UNINSTALL else "2.0",
            "reviewed-metadata",
            (f"Native {action.value} {spec.native_id}",),
        )

    def run(self, command, *, approval=None, cancel=None, observe=None):
        self.threads.append(threading.get_ident())
        if not command.mutates:
            now = utc_now()
            return CommandResult(command.preview(), now, now, 0, "tool version 1.0")
        assert approval and approval.permits(command)
        self.mutations.append(command)
        self.entered.set()
        if observe:
            observe(OutputSnapshot("stdout", "safe native output"))
        error = self.failure
        if self.block:
            while not self.release.wait(0.01):
                if cancel and cancel.is_set():
                    error = ErrorCode.CANCELLED
                    break
        if self.mutate and error is None:
            if command.argv[1] == "uninstall":
                self.installed.pop(command.argv[-1], None)
            else:
                self.install(
                    command.argv[-1],
                    "2.0",
                    "homebrew-cask" if "--cask" in command.argv else "homebrew-core",
                )
        now = utc_now()
        return CommandResult(
            command.preview(),
            now,
            now,
            1 if error else 0,
            "safe native output",
            "",
            False,
            OperationError(error, "Synthetic native failure.") if error else None,
        )
