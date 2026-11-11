"""Launch the documented read-only commands in isolated Python processes."""

import subprocess
import sys
import tempfile
import unittest

from tests.paths import PROJECT_ROOT


class AppEntryTests(unittest.TestCase):
    def test_source_launcher_resolves_catalog_from_another_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [
                    sys.executable,
                    "-S",
                    str(PROJECT_ROOT / "main.py"),
                    "catalog",
                    "validate",
                ],
                cwd=directory,
                capture_output=True,
                text=True,
                timeout=10,
                shell=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("14 packages and 39 reviewed mappings", result.stdout)

    def test_module_entry_point_needs_no_installed_dependencies(self):
        result = subprocess.run(
            [sys.executable, "-S", "-m", "app", "catalog", "search", "git"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Git (git)", result.stdout)

    def test_catalog_startup_never_imports_legacy_or_uses_processes_or_network(self):
        script = """
import importlib.abc
import sys
sys.path.insert(0, sys.argv[1])
class BlockLegacy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('app.legacy') or fullname in {'requests', 'distro', 'PySide6'}:
            raise AssertionError('unexpected optional import: ' + fullname)
sys.meta_path.insert(0, BlockLegacy())
def deny_side_effects(event, args):
    if event in {'subprocess.Popen', 'os.system', 'socket.connect', 'socket.getaddrinfo'}:
        raise AssertionError('unexpected process or network access')
sys.addaudithook(deny_side_effects)
from app.main import main
raise SystemExit(main(['catalog', 'validate']))
"""
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-c", script, str(PROJECT_ROOT)],
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Validated 14 packages", result.stdout)
