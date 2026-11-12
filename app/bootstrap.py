"""Wire the optional desktop to the existing core; no imports run native commands."""

from services.catalog.loader import load_catalog
from services.catalog.service import CatalogService
from services.desktop import DesktopServices
from services.discovery import DiscoveryService
from services.inventory import Inspector
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
    from PySide6.QtWidgets import QApplication, QMessageBox

    from app.ui.main_window import MainWindow
    from system.state import default_state_path

    application = QApplication.instance()
    if application is None:
        application = QApplication(["dev-setup"])
    if not isinstance(application, QApplication):
        raise RuntimeError("Desktop requires a QApplication.")
    application.setApplicationName("Dev Setup")
    application.setStyle("Fusion")
    try:
        services = create_services()
    except Exception:
        QMessageBox.critical(
            None,
            "Dev Setup",
            "The local catalog or platform could not be loaded. Run catalog validation to inspect the issue.",
        )
        return 1
    window = MainWindow(services, state_path=default_state_path())
    window.show()
    status = application.exec()
    # An external application.quit() can bypass the normal close-event guard.
    # Keep the window/controller alive until their cancellable worker has stopped.
    window.pending_close = True
    window.controller.cancel()
    window.environment.controller.cancel()
    window.search_controller.cancel()
    window.support.controller.cancel()
    job = window.controller.job
    if job is not None:
        job.wait()
    environment_job = window.environment.controller.job
    if environment_job is not None:
        environment_job.wait()
    search_job = window.search_controller.job
    if search_job is not None:
        search_job.wait()
    support_job = window.support.controller.job
    if support_job is not None:
        support_job.wait()
    application.processEvents()
    window.state_controller.close()
    window.close()
    return status
