"""Tests for version detection functionality in system_info.py"""
import subprocess
import unittest
from unittest.mock import MagicMock, patch

from system_info import get_tool_version, get_installed_tools


class TestVersionDetection(unittest.TestCase):
    """Test version detection for installed tools."""

    def test_get_tool_version_git(self):
        """Test version extraction for Git-style output."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                stdout="git version 2.51.0\n",
                stderr="",
                returncode=0
            )
            
            version = get_tool_version("git", ["--version"])
            self.assertEqual(version, "2.51.0")

    def test_get_tool_version_python(self):
        """Test version extraction for Python-style output."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                stdout="Python 3.13.5\n",
                stderr="",
                returncode=0
            )
            
            version = get_tool_version("python", ["--version"])
            self.assertEqual(version, "3.13.5")

    def test_get_tool_version_node(self):
        """Test version extraction for Node.js-style output."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                stdout="v22.15.0\n",
                stderr="",
                returncode=0
            )
            
            version = get_tool_version("node", ["--version"])
            self.assertEqual(version, "22.15.0")

    def test_get_tool_version_vscode(self):
        """Test version extraction for VS Code-style output."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                stdout="1.102.0\ncommit-hash\nx64\n",
                stderr="",
                returncode=0
            )
            
            version = get_tool_version("code", ["--version"])
            self.assertEqual(version, "1.102.0")

    def test_get_tool_version_not_found(self):
        """Test graceful handling when tool is not found."""
        with patch('subprocess.run', side_effect=FileNotFoundError):
            version = get_tool_version("nonexistent", ["--version"])
            self.assertIsNone(version)

    def test_get_tool_version_timeout(self):
        """Test graceful handling of command timeout."""
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired("cmd", 5)):
            version = get_tool_version("slow_tool", ["--version"])
            self.assertIsNone(version)

    def test_get_tool_version_command_error(self):
        """Test graceful handling of command execution errors."""
        with patch('subprocess.run', side_effect=subprocess.CalledProcessError(1, "cmd")):
            version = get_tool_version("broken_tool", ["--version"])
            self.assertIsNone(version)

    def test_get_tool_version_no_version_pattern(self):
        """Test fallback when no version pattern is found."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                stdout="Some Tool - Custom Output\n",
                stderr="",
                returncode=0
            )
            
            version = get_tool_version("custom_tool", ["--version"])
            # Should return first line as fallback
            self.assertEqual(version, "Some Tool - Custom Output")

    def test_get_tool_version_stderr_output(self):
        """Test version extraction from stderr (some tools use stderr)."""
        with patch('subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                stdout="",
                stderr="Tool version 4.5.6\n",
                returncode=0
            )
            
            version = get_tool_version("tool", ["--version"])
            self.assertEqual(version, "4.5.6")

    def test_get_installed_tools_structure(self):
        """Test that get_installed_tools returns correct structure."""
        with patch('shutil.which') as mock_which, \
             patch('system_info.get_tool_version') as mock_get_version:
            
            # Mock Git as installed with version
            mock_which.side_effect = lambda cmd: "/usr/bin/git" if cmd == "git" else None
            mock_get_version.return_value = "2.51.0"
            
            tools = get_installed_tools()
            
            # Check structure: list of tuples (name, is_installed, version)
            self.assertIsInstance(tools, list)
            self.assertTrue(all(len(tool) == 3 for tool in tools))
            
            # First tool should be Git
            git_info = tools[0]
            self.assertEqual(git_info[0], "Git")
            self.assertTrue(git_info[1])  # is_installed
            self.assertEqual(git_info[2], "2.51.0")  # version

    def test_get_installed_tools_not_installed(self):
        """Test that uninstalled tools have None version."""
        with patch('shutil.which', return_value=None):
            tools = get_installed_tools()
            
            # All tools should be marked as not installed
            for name, is_installed, version in tools:
                self.assertFalse(is_installed)
                self.assertIsNone(version)


if __name__ == '__main__':
    unittest.main()
