"""Wire the optional desktop to the existing core; no imports run native commands."""

from services.catalog.loader import load_catalog
from services.catalog.service import CatalogService
from services.desktop import DesktopServices
from services.inventory import Inspector
from system.platform import detect_platform
from system.runner import CommandRunner


def create_services() -> DesktopServices:
    from package_managers.apt import APT
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
    return DesktopServices(
        CatalogService(load_catalog()), host, adapters, tuple(inspectors), runner
    )


def launch() -> int:
    from PySide6.QtWidgets import QApplication, QMessageBox

    from app.ui.main_window import MainWindow

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
    window = MainWindow(services)
    window.show()
    status = application.exec()
    # An external application.quit() can bypass the normal close-event guard.
    # Keep the window/controller alive until their cancellable worker has stopped.
    window.pending_close = True
    window.controller.cancel()
    job = window.controller.job
    if job is not None:
        job.wait()
    application.processEvents()
    window.close()
    return status
