"""Application logging.

Records installation events - attempts, successes, and failures - to
logs/setup.log with timestamps, so a run can be reviewed afterwards.

Logging is best-effort: if the log file cannot be created (for example, no
write permission), the application keeps working without it.
"""
import logging
import os

LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "setup.log")

_logger = None


def get_logger():
    """Return the shared application logger, configuring it on first use."""
    global _logger
    if _logger is not None:
        return _logger

    logger = logging.getLogger("dev_setup")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("[%(asctime)s]\n%(message)s\n", datefmt="%Y-%m-%d %H:%M:%S")
        )
        logger.addHandler(handler)
    except OSError:
        # Logging must never stop an installation from running.
        logger.addHandler(logging.NullHandler())

    _logger = logger
    return logger
