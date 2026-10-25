"""Application configuration (environment variables only; no secrets in code)."""
from __future__ import annotations

import logging
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
VAR_DIR = Path(os.environ.get("AGENTHILL_VAR_DIR", ROOT / "var"))
DATABASE_URL = os.environ.get("AGENTHILL_DATABASE_URL", f"sqlite:///{VAR_DIR / 'agenthill.db'}")
FRONTEND_DIST = ROOT / "frontend" / "dist"
DEFAULT_OLLAMA_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")


class SecretScrubber(logging.Filter):
    """Removes any configured API key value from log records."""

    def __init__(self, secrets_fn):
        super().__init__()
        self.secrets_fn = secrets_fn

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for s in self.secrets_fn():
            if s and len(s) > 6 and s in msg:
                msg = msg.replace(s, "[REDACTED-SECRET]")
                record.msg, record.args = msg, ()
        return True
