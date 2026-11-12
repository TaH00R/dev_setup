"""Project configuration never silently replaces unrelated or stale files."""

import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from services.environments import ProjectConfigService


class ProjectConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.project = Path(self.directory.name)
        self.service = ProjectConfigService()

    def test_preview_is_inert_and_only_exact_consent_writes(self):
        plan = self.service.preview(self.project, "python", "3.13.2")
        self.assertFalse(plan.path.exists())
        for approved in (False, 1, None):
            with self.assertRaises(ValueError):
                self.service.apply(plan, approved)
        with self.assertRaises(ValueError):
            self.service.apply(replace(plan, content="malicious"), True)
        self.assertEqual(self.service.apply(plan, True).read_text(), "3.13.2\n")
        with self.assertRaises(ValueError):
            self.service.apply(plan, True)

    def test_changed_and_new_files_require_fresh_preview(self):
        plan = self.service.preview(self.project, "python", "3.13.2")
        plan.path.write_text("3.12.9\n")
        with self.assertRaises(ValueError):
            self.service.apply(plan, True)
        self.assertEqual(plan.path.read_text(), "3.12.9\n")

    def test_mise_choices_merge_and_unmanaged_content_is_preserved(self):
        first = self.service.preview(self.project, "mise-python", "3.13.2")
        self.service.apply(first, True)
        second = self.service.preview(self.project, "mise-node", "22.14.0")
        self.assertIn('python = "3.13.2"', second.content)
        self.assertIn('node = "22.14.0"', second.content)
        second.path.write_text('[env]\nSECRET = "do-not-print"\n')
        with self.assertRaises(ValueError):
            self.service.preview(self.project, "mise-node", "22.14.0")

    def test_environment_only_accepts_reviewed_relative_paths(self):
        for name, value in (
            ("TOKEN", "tools"),
            ("PATH", "tools"),
            ("JAVA_HOME", "../outside"),
            ("JAVA_HOME", "C:/secret"),
            ("JAVA_HOME", "/outside"),
            ("JAVA_HOME", "$(whoami)"),
        ):
            with self.assertRaises(ValueError):
                self.service.preview_environment(self.project, name, value)
        plan = self.service.preview_environment(
            self.project, "JAVA_HOME", ".tools/java"
        )
        self.assertEqual(plan.path.name, ".dev-setup.env")
        self.assertNotIn(str(self.project), plan.content)
        self.service.apply(plan, True)
        self.assertFalse((self.project / ".env").exists())

    def test_linked_project_or_target_is_refused(self):
        target = self.project / "real"
        target.mkdir()
        link = self.project / "linked"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation is not available on this host.")
        with self.assertRaises(ValueError):
            self.service.preview(link, "python", "3.13.2")
        (self.project / ".python-version").symlink_to(target / "version")
        with self.assertRaises(ValueError):
            self.service.preview(self.project, "python", "3.13.2")

    def test_unmanaged_rust_configuration_refused(self):
        (self.project / "rust-toolchain.toml").write_text(
            '[toolchain]\nchannel="stable"\ncomponents=["clippy"]\n'
        )
        with self.assertRaises(ValueError):
            self.service.preview(self.project, "rust", "1.85.0")

    def test_managed_header_does_not_authorize_discarding_extra_rust_settings(self):
        plan = self.service.preview(self.project, "rust", "1.85.0")
        self.service.apply(plan, True)
        with plan.path.open("a") as stream:
            stream.write('components = ["clippy"]\n')
        with self.assertRaises(ValueError):
            self.service.preview(self.project, "rust", "1.86.0")


if __name__ == "__main__":
    unittest.main()
