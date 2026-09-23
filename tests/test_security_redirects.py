"""Redirects must not re-send a credential-bearing request body to another origin.

httpx strips ``Authorization`` on a cross-origin redirect, but a 307/308 re-sends the original
POST body, which for this server is a token request carrying ``client_secret``,
``device_code`` or ``refresh_token``.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from ga4gh_mcp.auth.manager import AuthError, AuthManager
from ga4gh_mcp.auth.store import TokenStore
from ga4gh_mcp.config import Settings
from ga4gh_mcp.http import AsyncHttp

pytestmark = pytest.mark.asyncio

EVIL = "https://attacker-idp.test"


def _mgr(tmp_path, **kw):
    http = AsyncHttp(timeout=5, max_retries=0)
    return AuthManager(Settings(**kw), http, TokenStore(path=tmp_path / "t.json")), http


@pytest.fixture
def secret_env(monkeypatch):
    monkeypatch.setenv("GA4GH_MCP_CLIENT_ID_SVC_ORG", "cid")
    monkeypatch.setenv("GA4GH_MCP_CLIENT_SECRET_SVC_ORG", "c-secret")


@respx.mock
async def test_configured_token_endpoint_307_does_not_resend_secret(tmp_path, secret_env):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("hosts:\n  svc.org:\n    oauth:\n      token_endpoint: https://idp.test/token\n")
    respx.post("https://idp.test/token").mock(return_value=httpx.Response(
        307, headers={"Location": f"{EVIL}/token"}))
    sink = respx.post(f"{EVIL}/token").mock(return_value=httpx.Response(
        200, json={"access_token": "x", "expires_in": 60}))
    mgr, http = _mgr(tmp_path, config_file=str(cfg))
    with pytest.raises(AuthError):
        await mgr.client_credentials("https://svc.org", "drs")
    await http.close()
    assert not sink.called


@respx.mock
async def test_cross_origin_redirect_is_followed_without_authorization():
    respx.get("https://svc.org/a").mock(return_value=httpx.Response(
        302, headers={"Location": "https://other.test/b"}))
    sink = respx.get("https://other.test/b").mock(return_value=httpx.Response(200, json={"x": 1}))
    http = AsyncHttp(timeout=5, max_retries=0)
    r = await http.get_json("https://svc.org/a", headers={"Authorization": "Bearer t"})
    await http.close()
    assert r.json == {"x": 1}
    assert "authorization" not in sink.calls[0].request.headers
