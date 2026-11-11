"""Enforce a clean import boundary in a fresh interpreter."""

import subprocess
import sys
import unittest
from pathlib import Path


class CoreImportTests(unittest.TestCase):
    def test_import_without_site_packages_or_legacy_modules(self):
        root = Path(__file__).resolve().parents[1]
        # -I excludes user Python paths; -S excludes installed site packages.
        script = """
import importlib.abc
import sys

class RejectExternalLayers(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {
            'main', 'installers', 'system_info', 'PySide6', 'requests', 'distro'
        }:
            raise AssertionError('Core imported forbidden layer: ' + fullname)

sys.meta_path.insert(0, RejectExternalLayers())
sys.path.insert(0, sys.argv[1])

def deny_process(event, args):
    if event in {'subprocess.Popen', 'os.system', 'os.posix_spawn'}:
        raise AssertionError('Core import started a process')

sys.addaudithook(deny_process)
import dev_setup
assert dev_setup.__file__ == sys.argv[2]
"""
        result = subprocess.run(
            [sys.executable, "-I", "-S", "-c", script, str(root),
             str(root / "dev_setup" / "__init__.py")],
            capture_output=True, text=True, timeout=10, shell=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
