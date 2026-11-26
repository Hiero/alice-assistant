"""Opt-in debug log of what Alice hears, calls and answers. Off unless ALICE_DEBUG=1.

It records whole conversations, so the file is private to the user and rotates to stay small.
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

ENABLED = os.environ.get("ALICE_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
STATE_HOME = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
LOG_FILE = Path(os.environ.get("ALICE_DEBUG_LOG", STATE_HOME / "alice/debug.log"))
MAX_CHARS = int(os.environ.get("ALICE_DEBUG_MAX_CHARS", "4000"))

log = logging.getLogger("alice")
log.propagate = False


class PrivateFileHandler(RotatingFileHandler):
    """Every log file, including new ones after rotating, is readable only by the user."""

    def _open(self):
        stream = super()._open()
        os.chmod(self.baseFilename, 0o600)
        return stream


def setup() -> None:
    if not ENABLED:
        log.addHandler(logging.NullHandler())
        log.setLevel(logging.WARNING)
        return
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    handler = PrivateFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)


def clip(value: object) -> str:
    """Long tool results, like a day's Steam list, are cut so the log stays readable."""
    text = str(value)
    return text if len(text) <= MAX_CHARS else f"{text[:MAX_CHARS]}… [{len(text) - MAX_CHARS} more characters]"
