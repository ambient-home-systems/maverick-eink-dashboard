"""Logging configuration."""

from __future__ import annotations

import logging
import os
import sys


class _Formatter(logging.Formatter):
    """Compact, aligned output. Colour only when attached to a terminal."""

    COLOURS = {
        "DEBUG": "\033[2;37m",
        "INFO": "\033[0;36m",
        "WARNING": "\033[0;33m",
        "ERROR": "\033[0;31m",
        "CRITICAL": "\033[1;31m",
    }
    RESET = "\033[0m"

    def __init__(self, colour: bool) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)-24s %(message)s", "%H:%M:%S")
        self._colour = colour

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if self._colour:
            prefix = self.COLOURS.get(record.levelname, "")
            return f"{prefix}{text}{self.RESET}" if prefix else text
        return text


def setup_logging(level: str = "info") -> None:
    handler = logging.StreamHandler(sys.stderr)
    # NO_COLOR is the de facto standard; add-on logs are not a terminal.
    colour = sys.stderr.isatty() and not os.environ.get("NO_COLOR")
    handler.setFormatter(_Formatter(colour))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # These are chatty at DEBUG and say nothing useful about rendering.
    for noisy in ("httpx", "httpcore", "websockets", "apscheduler", "asyncio", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


__all__ = ["setup_logging"]
