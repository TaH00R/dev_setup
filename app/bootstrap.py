"""Wire the terminal interface to the existing core; no imports run native commands."""

from models.diagnostics import DiagnosticCode, DiagnosticComponent
from services.catalog.loader import load_catalog
from services.catalog.service import CatalogService
from services.desktop import DesktopServices
from services.discovery import DiscoveryService
from services.inventory import Inspector
from system.diagnostics import record_diagnostic
from system.platform import detect_platform
from system.runner import CommandRunner


def create_services() -> DesktopServices:
    from package_managers.apt import APT
    from package_managers.discovery import providers_for
    from package_managers.homebrew import Homebrew
    from package_managers.inspection import APTInspector, HomebrewInspector
    from package_managers.registry import adapters_for

    host = detect_platform()
    runner = CommandRunner()
    adapters = adapters_for(host)
    inspectors: list[Inspector] = []
    for adapter in adapters:
        if isinstance(adapter, APT):
            inspectors.append(APTInspector(adapter, runner))
        elif isinstance(adapter, Homebrew):
            inspectors.append(HomebrewInspector(adapter, runner))
    catalog = CatalogService(load_catalog())
    return DesktopServices(
        catalog,
        host,
        adapters,
        tuple(inspectors),
        runner,
        DiscoveryService(catalog, host, providers_for(adapters, runner)),
    )


def launch() -> int:
    import sys

    from app.tui.app import DevSetupApp
    from system.state import default_state_path

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print(
            "Open Dev Setup in an interactive terminal, or use catalog commands / --smoke-test.",
            file=sys.stderr,
        )
        return 1
    try:
        services = create_services()
    except Exception:
        record_diagnostic(DiagnosticCode.STARTUP_FAILED, DiagnosticComponent.STARTUP)
        print(
            "The catalog or platform could not be loaded. Run catalog validate.",
            file=sys.stderr,
        )
        return 1
    DevSetupApp(services, state_path=default_state_path()).run()
    return 0
