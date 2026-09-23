"""WES type-aware tools."""

from __future__ import annotations

from ..annotations import READ_REMOTE
from ..context import ctx
from ..errors import ok, safe_tool


def register(mcp) -> None:
    @mcp.tool(annotations=READ_REMOTE)
    @safe_tool
    async def wes_get_service_info(service_id_or_url: str) -> dict:
        """Get a WES service's capabilities: supported workflow types/versions, engines,
        filesystem protocols, and current run-state counts."""
        c = ctx()
        resolved = await c.resolve(service_id_or_url, "wes")
        data = await c.wes.get_service_info(resolved.url)
        return ok(data)

    @mcp.tool(annotations=READ_REMOTE)
    @safe_tool
    async def wes_list_runs(service_id_or_url: str, page_size: int = 20,
                            page_token: str | None = None) -> dict:
        """List workflow runs on a WES service (usually requires authentication)."""
        c = ctx()
        resolved = await c.resolve(service_id_or_url, "wes")
        data = await c.wes.list_runs(resolved.url, page_size=page_size, page_token=page_token)
        return ok(data)

    @mcp.tool(annotations=READ_REMOTE)
    @safe_tool
    async def wes_get_run(service_id_or_url: str, run_id: str) -> dict:
        """Get the status, logs summary, and outputs of a specific WES run (usually requires auth)."""
        c = ctx()
        resolved = await c.resolve(service_id_or_url, "wes")
        data = await c.wes.get_run(resolved.url, run_id)
        return ok(data)
