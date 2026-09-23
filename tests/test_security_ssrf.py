"""Hosted (HTTP transport) deployments must not fetch internal / metadata addresses.

Every tool accepts a raw URL, and ``service_request`` returns the upstream body. Served over
HTTP (Cloud Run / Vertex / Bedrock per docs/clients), that reads the cloud metadata service
(169.254.169.254) and other internal-only endpoints on behalf of any caller.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from ga4gh_mcp.config import Settings
from ga4gh_mcp.server import build_server

pytestmark = pytest.mark.asyncio

META = "http://169.254.169.254/latest/meta-data/iam/security-credentials"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GA4GH_MCP_HOME", str(tmp_path / "home"))


async def _call(mcp, name, args):
    r = await mcp.call_tool(name, args)
    if isinstance(r, tuple):
        content, raw = r
        if raw is not None:
            return raw
        r = content
    return json.loads(r[0].text)


async def _request(settings, url, path):
    mcp, ctx = build_server(settings)
    try:
        return await _call(mcp, "service_request", {"service_id_or_url": url, "path": path})
    finally:
        await ctx.aclose()


@respx.mock
async def test_http_transport_refuses_metadata_address():
    route = respx.get(f"{META}/role").mock(return_value=httpx.Response(200, json={"AccessKeyId": "AK"}))
    res = await _request(Settings(transport="http", max_retries=0), META, "/role")
    assert not route.called
    assert res["ok"] is False


@respx.mock
async def test_http_transport_refuses_redirect_into_loopback():
    respx.get("https://public.test/x").mock(return_value=httpx.Response(
        302, headers={"Location": "http://127.0.0.1:9000/admin"}))
    route = respx.get("http://127.0.0.1:9000/admin").mock(return_value=httpx.Response(200, json={}))
    await _request(Settings(transport="http", max_retries=0), "https://public.test", "/x")
    assert not route.called


@respx.mock
async def test_stdio_default_and_explicit_opt_out_still_reach_private_addresses():
    route = respx.get("http://127.0.0.1:9000/x").mock(return_value=httpx.Response(200, json={"a": 1}))
    for s in (Settings(max_retries=0),
              Settings(transport="http", block_private_addresses=False, max_retries=0)):
        res = await _request(s, "http://127.0.0.1:9000", "/x")
        assert res["ok"] is True
    assert route.call_count == 2
