"""Structured logging configuration for the Protein Friend Finder backend.

All modules should import the `logger` from this module rather than
creating their own logger.  This guarantees a single, consistent
format across every log line in the console.
"""

import logging
import sys
from typing import Optional

# ANSI colour codes for terminal output
_COLORS = {
    "DEBUG": "\033[36m",     # cyan
    "INFO": "\033[32m",     # green
    "WARNING": "\033[33m",  # yellow
    "ERROR": "\033[31m",    # red
    "CRITICAL": "\033[35m", # magenta
    "RESET": "\033[0m",
}


class ColouredFormatter(logging.Formatter):
    """Log formatter with colour-coded level names and a structured layout."""

    def __init__(self, show_timestamp: bool = True):
        self.show_timestamp = show_timestamp
        super().__init__()

    def format(self, record: logging.LogRecord) -> str:
        level = record.levelname
        color = _COLORS.get(level, _COLORS["RESET"])
        reset = _COLORS["RESET"]

        if self.show_timestamp:
            ts = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
            prefix = f"[{ts}]"
        else:
            prefix = ""

        # Build a clean message
        if record.args:
            msg = record.msg % record.args
        else:
            msg = record.getMessage()

        # Include module name for context
        logger_name = record.name or "root"
        short_name = logger_name.split(".")[-1] if logger_name else "root"

        # Assemble final line
        line = f"{prefix} {color}{level}{reset} {short_name} — {msg}"
        return line


def setup_logging(
    level: int = logging.INFO,
    show_timestamp: bool = True,
    log_to_file: Optional[str] = None,
) -> logging.Logger:
    """Configure and return the root logger for the application.

    Args:
        level: Minimum log level to display.
        show_timestamp: Whether to prepend timestamps to log lines.
        log_to_file: Optional file path to also write logs to.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Remove any existing handlers to avoid duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Console handler with colour formatter
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(
        ColouredFormatter(show_timestamp=show_timestamp)
    )
    console_handler.setLevel(level)
    root_logger.addHandler(console_handler)

    # Optional file handler
    if log_to_file:
        file_handler = logging.FileHandler(log_to_file)
        file_handler.setFormatter(
            logging.Formatter(
                "[%(asctime)s] %(levelname)-8s %(name)s — %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        file_handler.setLevel(level)
        root_logger.addHandler(file_handler)

    # Prevent propagation to avoid double-logging in some setups
    root_logger.propagate = False

    return root_logger


# Set up default configuration on import
setup_logging()

# Log startup
logger = logging.getLogger(__name__)
logger.info("=" * 60)
logger.info("Protein Friend Finder backend logging initialized")
logger.info("=" * 60)
