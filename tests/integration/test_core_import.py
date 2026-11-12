"""Enforce a clean import boundary in a fresh interpreter."""

import subprocess
import sys
import unittest

from tests.paths import PROJECT_ROOT


class CoreImportTests(unittest.TestCase):
    def test_import_without_site_packages_or_legacy_modules(self):
        root = PROJECT_ROOT
        # -I excludes user Python paths; -S excludes installed site packages.
        script = """
import importlib.abc
import importlib
import pkgutil
import sys

class RejectExternalLayers(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {
            'app', 'main', 'installers', 'system_info', 'PySide6', 'requests', 'distro'
        }:
            raise AssertionError('Core imported forbidden layer: ' + fullname)

sys.meta_path.insert(0, RejectExternalLayers())
sys.path.insert(0, sys.argv[1])

def deny_process(event, args):
    if event in {'subprocess.Popen', 'os.system', 'os.posix_spawn',
                 'socket.connect', 'socket.getaddrinfo'}:
        raise AssertionError('Core import started a process or network request')

sys.addaudithook(deny_process)
from pathlib import Path
for name in ('models', 'system', 'package_managers', 'runtimes', 'services'):
    package = importlib.import_module(name)
    assert Path(package.__file__) == Path(sys.argv[1]) / name / '__init__.py'
    for module in pkgutil.walk_packages(package.__path__, package.__name__ + '.'):
        importlib.import_module(module.name)
"""
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-c",
                script,
                str(root),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
