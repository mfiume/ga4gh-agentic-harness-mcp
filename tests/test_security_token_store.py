"""The on-disk token cache is created private, not made private after the fact."""

from __future__ import annotations

import os
import stat

from ga4gh_mcp.auth.store import TokenStore


def test_token_file_is_created_0600_without_relying_on_chmod(tmp_path, monkeypatch):
    # With chmod neutralised, the mode the file is *created* with is what another local user
    # can read between write and chmod.
    monkeypatch.setattr(os, "chmod", lambda *a, **k: None)
    old = os.umask(0o022)
    try:
        path = tmp_path / "home" / "tokens.json"
        TokenStore(path=path).set("h", access_token="AT", refresh_token="RT")
    finally:
        os.umask(old)
    assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
    assert stat.S_IMODE(path.parent.stat().st_mode) & 0o077 == 0
