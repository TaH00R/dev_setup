"""Current application entry point; catalog commands never launch installers."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from services.catalog.loader import load_catalog
from services.catalog.schema import CATEGORIES, CatalogValidationError
from services.catalog.service import CatalogService


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dev-setup",
        description="Explore the Dev Setup catalog. The desktop interface is in development.",
    )
    commands = parser.add_subparsers(dest="command")
    catalog = commands.add_parser(
        "catalog", help="Read or validate local package metadata"
    )
    catalog_commands = catalog.add_subparsers(dest="catalog_command", required=True)
    validate = catalog_commands.add_parser(
        "validate", help="Check all package definitions"
    )
    search = catalog_commands.add_parser(
        "search", help="Search the curated catalog offline"
    )
    search.add_argument(
        "query", nargs="?", default="", help="Name, tag or native package ID"
    )
    search.add_argument("--category", choices=sorted(CATEGORIES))
    for command in (validate, search):
        command.add_argument(
            "--directory", type=Path, help="Read another local catalog directory"
        )
    commands.add_parser(
        "legacy", help="Open the preserved installer menu (can change your system)"
    )
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 0
    if args.command == "legacy":
        # Keep old dependencies and installer import side effects out of normal
        # startup, catalog commands, and the GUI-independent core.
        try:
            from app.legacy.cli import main as legacy_main
        except ModuleNotFoundError as error:
            if error.name not in {"requests", "distro"}:
                raise
            print(
                "The legacy menu needs its optional dependencies. Run: python -m pip install -r requirements.txt",
                file=sys.stderr,
            )
            return 1
        legacy_main()
        return 0
    try:
        packages = load_catalog(args.directory)
    except CatalogValidationError as error:
        print(f"Catalog validation failed: {error}", file=sys.stderr)
        return 1
    if args.catalog_command == "validate":
        count = sum(len(package.install_specs) for package in packages)
        print(f"Validated {len(packages)} packages and {count} reviewed mappings.")
        return 0
    matches = CatalogService(packages).search(args.query, category=args.category)
    if not matches:
        print("No packages found.")
    for match in matches:
        package = match.package
        print(f"{package.name} ({package.id})")
        print(f"  {package.summary}")
        for spec in package.install_specs:
            print(
                f"  {spec.support.os.value} / {spec.backend} / {spec.source.id}: {spec.native_id}"
            )
    if matches:
        print(
            "\nCatalog mappings describe reviewed metadata; backend availability and installability still need a plan."
        )
    return 0
