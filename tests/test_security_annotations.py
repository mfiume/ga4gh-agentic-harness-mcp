"""Tools declare MCP ToolAnnotations, and state-changing HTTP methods are opt-in.

``service_request`` accepted POST against any service or raw URL with credentials attached,
which reaches WES POST /runs and POST /runs/{id}/cancel and TES POST /tasks and
POST /tasks/{id}:cancel, and no tool carried annotations, so a host had no signal to confirm.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from ga4gh_mcp.config import Settings
from ga4gh_mcp.server import build_server

pytestmark = pytest.mark.asyncio

STATEFUL = {"auth_set_token", "auth_login", "auth_revoke"}


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GA4GH_MCP_HOME", str(tmp_path / "home"))


async def _listed(settings):
    mcp, ctx = build_server(settings)
    try:
        return mcp, ctx, {t.name: t for t in await mcp.list_tools()}
    except Exception:
        await ctx.aclose()
        raise


async def test_every_tool_is_annotated():
    _, ctx, tools = await _listed(Settings())
    await ctx.aclose()
    for name, t in tools.items():
        a = t.annotations
        assert a is not None, name
        for hint in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"):
            assert getattr(a, hint) is not None, (name, hint)
        if name in STATEFUL:
            assert a.readOnlyHint is False, name
        else:
            assert a.readOnlyHint is True and a.destructiveHint is False, name
    assert tools["auth_revoke"].annotations.destructiveHint is True


async def test_service_request_annotated_destructive_only_when_writes_enabled():
    _, ctx, tools = await _listed(Settings(allow_write_methods=True))
    await ctx.aclose()
    a = tools["service_request"].annotations
    assert a.readOnlyHint is False and a.destructiveHint is True and a.idempotentHint is False


async def _call(mcp, name, args):
    r = await mcp.call_tool(name, args)
    if isinstance(r, tuple):
        content, raw = r
        if raw is not None:
            return raw
        r = content
    return json.loads(r[0].text)


@respx.mock
@pytest.mark.parametrize("allow", [False, True])
async def test_post_requires_operator_opt_in(allow):
    route = respx.post("https://wes.test/ga4gh/wes/v1/runs/r1/cancel").mock(
        return_value=httpx.Response(200, json={"run_id": "r1"}))
    mcp, ctx, _ = await _listed(Settings(allow_write_methods=allow, max_retries=0))
    try:
        res = await _call(mcp, "service_request", {
            "service_id_or_url": "https://wes.test/ga4gh/wes/v1", "path": "/runs/r1/cancel",
            "method": "POST", "artifact": "wes"})
    finally:
        await ctx.aclose()
    assert route.called is allow
    assert res["ok"] is allow
