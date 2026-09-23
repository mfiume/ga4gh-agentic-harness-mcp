"""MCP ToolAnnotations shared by the tool modules.

Hosts use these hints to decide what to confirm with the user, so every tool declares them.
"""

from __future__ import annotations

from mcp.types import ToolAnnotations


def annotations(*, read_only: bool, destructive: bool = False, idempotent: bool = True,
                open_world: bool = True) -> ToolAnnotations:
    return ToolAnnotations(readOnlyHint=read_only, destructiveHint=destructive,
                           idempotentHint=idempotent, openWorldHint=open_world)


#: Read-only tools that reach the registry or a remote GA4GH service.
READ_REMOTE = annotations(read_only=True)
#: Read-only tools answered from local state.
READ_LOCAL = annotations(read_only=True, open_world=False)
