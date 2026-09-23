"""Client secrets only go to token endpoints the operator configured.

``_endpoints_for`` filled token / device endpoints from OIDC discovery seeded by the service's
own ``WWW-Authenticate: Bearer realm=...`` (or its origin). A service, or anyone able to shape
that response, could therefore name an IdP on any host and receive the configured
``client_secret`` from ``auth_login`` (device code) or ``client_credentials``.
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


def _challenge_points_at(evil: str):
    respx.get("https://svc.org/ga4gh/drs/v1/objects/_ga4gh_mcp_auth_probe").mock(
        return_value=httpx.Response(401, headers={"WWW-Authenticate": f'Bearer realm="{evil}"'}))
    respx.get(f"{evil}/.well-known/openid-configuration").mock(return_value=httpx.Response(
        200, json={"issuer": evil, "token_endpoint": f"{evil}/token",
                   "device_authorization_endpoint": f"{evil}/device"}))


@pytest.fixture
def secret_env(monkeypatch):
    monkeypatch.setenv("GA4GH_MCP_CLIENT_ID_SVC_ORG", "cid")
    monkeypatch.setenv("GA4GH_MCP_CLIENT_SECRET_SVC_ORG", "c-secret")


@respx.mock
async def test_device_code_does_not_send_secret_to_discovered_endpoint(tmp_path, secret_env):
    _challenge_points_at(EVIL)
    device = respx.post(f"{EVIL}/device").mock(return_value=httpx.Response(200, json={
        "device_code": "dc", "user_code": "U", "verification_uri": f"{EVIL}/activate"}))
    mgr, http = _mgr(tmp_path)
    try:
        await mgr.begin_device_code("https://svc.org", "drs")
    except AuthError:
        pass
    await http.close()
    for call in device.calls:
        assert b"c-secret" not in call.request.content


@respx.mock
async def test_client_credentials_refuses_discovered_endpoint(tmp_path, secret_env):
    _challenge_points_at(EVIL)
    token = respx.post(f"{EVIL}/token").mock(return_value=httpx.Response(
        200, json={"access_token": "x", "expires_in": 60}))
    mgr, http = _mgr(tmp_path)
    with pytest.raises(AuthError):
        await mgr.client_credentials("https://svc.org", "drs")
    await http.close()
    assert not token.called


@respx.mock
async def test_client_credentials_uses_configured_endpoint(tmp_path, secret_env):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("hosts:\n  svc.org:\n    oauth:\n      token_endpoint: https://idp.test/token\n")
    token = respx.post("https://idp.test/token").mock(return_value=httpx.Response(
        200, json={"access_token": "x", "expires_in": 60}))
    mgr, http = _mgr(tmp_path, config_file=str(cfg))
    out = await mgr.client_credentials("https://svc.org", "drs")
    await http.close()
    assert out["host"] == "svc.org"
    assert b"c-secret" in token.calls[0].request.content
