"""Encrypted credential storage for LLM provider API keys.

Keys are stored only on the backend, encrypted with Fernet (AES-128-CBC + HMAC) in var/credentials.enc.
The encryption key comes from AGENTHILL_SECRET_KEY or a generated var/secret.key (chmod 600). Both
files are git-ignored. Keys are never returned by the API, never written to traces, runs or logs;
the API exposes only {configured, source}. Environment variables are honoured as a fallback.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

ENV_VARS = {"anthropic": ["ANTHROPIC_API_KEY"], "openai": ["OPENAI_API_KEY"], "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"]}


class CredentialStore:
    def __init__(self, var_dir: Path):
        self.var_dir = var_dir
        self.path = var_dir / "credentials.enc"
        self._lock = threading.Lock()

    def _fernet(self) -> Fernet:
        env_key = os.environ.get("AGENTHILL_SECRET_KEY")
        if env_key:
            return Fernet(env_key.encode())
        key_file = self.var_dir / "secret.key"
        if not key_file.exists():
            self.var_dir.mkdir(parents=True, exist_ok=True)
            key_file.write_bytes(Fernet.generate_key())
            os.chmod(key_file, 0o600)
        return Fernet(key_file.read_bytes().strip())

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self._fernet().decrypt(self.path.read_bytes()))
        except (InvalidToken, ValueError):
            return {}

    def _save(self, data: dict) -> None:
        self.var_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(self._fernet().encrypt(json.dumps(data).encode()))
        os.chmod(self.path, 0o600)

    def get(self, provider: str) -> str | None:
        with self._lock:
            stored = self._load().get(provider)
        if stored:
            return stored
        for var in ENV_VARS.get(provider, []):
            if os.environ.get(var):
                return os.environ[var]
        return None

    def set(self, provider: str, api_key: str) -> None:
        with self._lock:
            data = self._load()
            data[provider] = api_key.strip()
            self._save(data)

    def delete(self, provider: str) -> None:
        with self._lock:
            data = self._load()
            data.pop(provider, None)
            self._save(data)

    def status(self, provider: str) -> dict:
        with self._lock:
            stored = bool(self._load().get(provider))
        env = next((v for v in ENV_VARS.get(provider, []) if os.environ.get(v)), None)
        return {"configured": stored or bool(env), "source": "encrypted store" if stored else (f"env:{env}" if env else None)}

    def all_secrets(self) -> list[str]:
        with self._lock:
            vals = [v for v in self._load().values() if v]
        vals += [os.environ[v] for vs in ENV_VARS.values() for v in vs if os.environ.get(v)]
        return vals
