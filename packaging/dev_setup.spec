# PyInstaller spec: run through scripts/build_desktop.py from the repository.
import os
import sys
from pathlib import Path

root = Path(SPECPATH).parent
sys.path.insert(0, str(root))
from app import __version__
identity = os.environ.get("DEV_SETUP_MAC_SIGN_IDENTITY") if os.environ.get("DEV_SETUP_SIGNED_BUILD") == "1" else None
a = Analysis(
    [str(root / "scripts" / "desktop_entry.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / "catalog"), "catalog"),
           (str(root / "package_managers" / "scoop_bridge.ps1"), "package_managers")],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=["app.legacy", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="DevSetup",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=True,
          codesign_identity=identity)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="DevSetup")
if sys.platform == "darwin":
    app = BUNDLE(collection, name="DevSetup.app", icon=None,
                 version=__version__.split(".dev")[0],
                 bundle_identifier="org.devsetup.desktop",
                 codesign_identity=identity,
                 info_plist={"NSHighResolutionCapable": True})
