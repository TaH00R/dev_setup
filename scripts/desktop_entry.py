"""Frozen desktop entry point with bounded, side-effect-free bundle checks."""

import argparse
import sys
import traceback
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import __version__  # noqa: E402


def self_check() -> None:
    from services.catalog.loader import load_catalog
    from services.catalog.service import CatalogService
    from services.environments import starter_presets

    catalog = CatalogService(load_catalog())
    presets = starter_presets()
    if len(presets) != 10 or any(
        catalog.get(package.package_id) is None
        for preset in presets
        for package in preset.packages
    ):
        raise RuntimeError("Bundled presets do not match the bundled catalog.")


def smoke_test() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from app.bootstrap import create_services
    from app.ui.main_window import MainWindow

    # Explicit ephemeral services: never load the desktop's user-state path.
    application = QApplication(["dev-setup-smoke-test"])
    window = MainWindow(create_services(), auto_refresh=False)
    window.show()
    QTimer.singleShot(100, window.close)
    QTimer.singleShot(2000, application.quit)
    result = application.exec()
    application.processEvents()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Dev Setup desktop")
    parser.add_argument("--version", action="version", version=__version__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--self-check", action="store_true")
    modes.add_argument("--smoke-test", action="store_true")
    args = parser.parse_args()
    if args.self_check or args.smoke_test:
        try:
            self_check()
            return smoke_test() if args.smoke_test else 0
        except Exception:
            # Frozen windowed binaries have no stderr. Returning an exit status
            # prevents the bootloader opening an unattended exception dialog.
            if sys.stderr is not None:
                traceback.print_exc(file=sys.stderr)
            return 1
    from app.bootstrap import launch

    return launch()


if __name__ == "__main__":
    raise SystemExit(main())
