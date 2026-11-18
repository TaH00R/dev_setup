"""Standalone console entry point; the bundle includes its Python interpreter."""

import argparse
import asyncio
import sys
import traceback
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import __version__ as __version__  # noqa: E402


def self_check() -> None:
    from services.catalog.loader import load_catalog
    from services.catalog.service import CatalogService
    from services.environments import starter_presets

    catalog = CatalogService(load_catalog())
    if len(starter_presets()) != 10 or any(
        catalog.get(package.package_id) is None
        for preset in starter_presets()
        for package in preset.packages
    ):
        raise RuntimeError("Bundled presets do not match the bundled catalog.")
    from app.tui.app import DevSetupApp  # noqa: F401


def smoke_test() -> int:
    from textual.widgets import ContentSwitcher, DataTable, Input

    from app.bootstrap import create_services
    from app.tui.app import DevSetupApp
    from app.tui.pages import PAGES

    async def check() -> None:
        app = DevSetupApp(create_services(), auto_refresh=False)
        async with app.run_test(size=(120, 42)) as pilot:
            for _ in range(100):
                await pilot.pause(0.02)
                if not app.busy:
                    break
            assert app.query_one("#catalog", DataTable).row_count == 14
            assert app.theme == "textual-dark"
            if app.busy:
                raise RuntimeError("Standalone startup did not finish.")
            for page in PAGES:
                app.navigate(page)
                await pilot.pause()
                assert app.query_one("#pages", ContentSwitcher).current == page.lower()
            await pilot.resize_terminal(80, 24)
            await pilot.press("ctrl+f")
            assert app.focused is app.query_one("#search", Input)
            # Rendering must retain international text even with the host PATH empty.
            app.status(
                "D\u00e9veloppeur \u00b7 \u958b\u767a \u00b7 \uac1c\ubc1c \u00b7 \u0645\u0637\u0648\u0631"
            )
            await pilot.pause()
            await pilot.resize_terminal(140, 45)
            await pilot.press("ctrl+q")
            assert app.closing

    asyncio.run(check())
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--smoke-test", action="store_true")
    args, _ = parser.parse_known_args()
    if args.self_check or args.smoke_test:
        try:
            self_check()
            return smoke_test() if args.smoke_test else 0
        except Exception:
            if sys.stderr is not None:
                traceback.print_exc(file=sys.stderr)
            return 1
    from app.main import main as application_main

    return application_main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
