"""On-disk token cache (``~/.ga4gh-mcp/tokens.json``, mode 0600).

Stores per-host OAuth tokens so interactive logins persist across restarts.
Never stores anything unless a token is actually acquired.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ..config import config_home


class TokenStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path or (config_home() / "tokens.json")
        self._data: dict[str, dict] = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        try:
            if self._path.exists():
                self._data = json.loads(self._path.read_text())
        except Exception:  # noqa: BLE001 — corrupt store shouldn't crash the server
            self._data = {}
        self._loaded = True

    def _save(self) -> None:
        # Created private from the start (O_CREAT with 0600) rather than written with the
        # process umask and tightened afterwards.
        self._path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.unlink(missing_ok=True)  # O_EXCL below: never reuse a pre-existing, looser file
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(self._data, indent=2))
        tmp.replace(self._path)

    def get(self, host: str) -> dict | None:
        self._load()
        return self._data.get(host)

    def valid_access_token(self, host: str, skew: float = 60.0) -> str | None:
        entry = self.get(host)
        if not entry:
            return None
        exp = entry.get("expires_at")
        if exp is not None and time.time() > (exp - skew):
            return None
        return entry.get("access_token")

    def set(self, host: str, *, access_token: str, refresh_token: str | None = None,
            expires_at: float | None = None, source: str = "oauth", **extra) -> None:
        self._load()
        entry = {"access_token": access_token, "source": source}
        if refresh_token:
            entry["refresh_token"] = refresh_token
        if expires_at:
            entry["expires_at"] = expires_at
        entry.update(extra)
        self._data[host] = entry
        self._save()

    def delete(self, host: str | None = None) -> None:
        self._load()
        if host is None:
            self._data = {}
        else:
            self._data.pop(host, None)
        self._save()

    def hosts(self) -> list[str]:
        self._load()
        return list(self._data.keys())
