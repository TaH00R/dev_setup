"""Read-only catalog validation: python -m catalog validate."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .loader import load_catalog
from .schema import CatalogValidationError


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate curated package metadata without executing package operations."
    )
    parser.add_argument("command", choices=("validate",), nargs="?", default="validate")
    parser.add_argument(
        "--directory",
        type=Path,
        help="Local catalog directory; defaults to the bundled catalog",
    )
    args = parser.parse_args(argv)
    try:
        packages = load_catalog(args.directory)
    except CatalogValidationError as error:
        print(f"Catalog validation failed: {error}", file=sys.stderr)
        return 1
    count = sum(len(package.install_specs) for package in packages)
    print(f"Validated {len(packages)} packages and {count} reviewed mappings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
