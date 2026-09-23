"""TRS type-aware tools."""

from __future__ import annotations

from ..annotations import READ_REMOTE
from ..context import ctx
from ..errors import ok, safe_tool


def register(mcp) -> None:
    @mcp.tool(annotations=READ_REMOTE)
    @safe_tool
    async def trs_list_tools(service_id_or_url: str, tool_class: str | None = None,
                             organization: str | None = None, limit: int = 20) -> dict:
        """List tools/workflows registered in a TRS service (e.g. Dockstore).

        Optionally filter by ``tool_class`` (e.g. "Workflow", "CommandLineTool")
        and ``organization``.
        """
        c = ctx()
        resolved = await c.resolve(service_id_or_url, "trs")
        data = await c.trs.list_tools(resolved.url, toolClass=tool_class,
                                      organization=organization, limit=limit)
        return ok(data)

    @mcp.tool(annotations=READ_REMOTE)
    @safe_tool
    async def trs_get_tool(service_id_or_url: str, tool_id: str) -> dict:
        """Get details of a specific TRS tool including its versions and container images."""
        c = ctx()
        resolved = await c.resolve(service_id_or_url, "trs")
        data = await c.trs.get_tool(resolved.url, tool_id)
        return ok(data)
