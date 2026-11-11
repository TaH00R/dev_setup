"""Tests for the application logging helper.

Run from the repository root with:

    python -m unittest discover -s tests
"""
import logging
import os
import shutil
import tempfile
import unittest

from app.legacy.installers import logger as logger_module


class LoggerTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._orig_dir = logger_module.LOG_DIR
        self._orig_file = logger_module.LOG_FILE
        logger_module.LOG_DIR = self.tmp
        logger_module.LOG_FILE = os.path.join(self.tmp, "setup.log")
        logger_module._logger = None

    def tearDown(self):
        named = logging.getLogger("dev_setup")
        for handler in list(named.handlers):
            handler.close()
            named.removeHandler(handler)
        logger_module.LOG_DIR = self._orig_dir
        logger_module.LOG_FILE = self._orig_file
        logger_module._logger = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_creates_log_file(self):
        logger_module.get_logger().info("Installing Git")
        self.assertTrue(os.path.exists(logger_module.LOG_FILE))

    def test_entry_has_timestamp_and_message(self):
        log = logger_module.get_logger()
        log.info("Installing Git")
        for handler in log.handlers:
            handler.flush()
        with open(logger_module.LOG_FILE, encoding="utf-8") as stream:
            content = stream.read()
        self.assertRegex(content, r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]")
        self.assertIn("Installing Git", content)

    def test_get_logger_is_singleton(self):
        self.assertIs(logger_module.get_logger(), logger_module.get_logger())

    def test_logging_never_raises_when_dir_unwritable(self):
        # Point the log at a path that cannot be created, and confirm it
        # falls back silently instead of raising.
        logger_module._logger = None
        logger_module.LOG_DIR = os.path.join(self.tmp, "setup.log", "nested")
        logger_module.LOG_FILE = os.path.join(logger_module.LOG_DIR, "setup.log")
        open(os.path.join(self.tmp, "setup.log"), "w").close()  # a file blocks the dir
        try:
            logger_module.get_logger().info("should not raise")
        except OSError:
            self.fail("get_logger must not raise when the log path is unusable")


if __name__ == "__main__":
    unittest.main()
