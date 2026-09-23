"""DRS type-aware tools."""

from __future__ import annotations

from ..annotations import READ_REMOTE
from ..context import ctx
from ..errors import ERR_NOT_FOUND, ToolError, ok, safe_tool


def register(mcp) -> None:
    @mcp.tool(annotations=READ_REMOTE)
    @safe_tool
    async def drs_get_object(service_id_or_url: str, object_id: str, expand: bool = False) -> dict:
        """Get DRS object metadata: size, checksums, MIME type, and access methods.

        ``service_id_or_url`` may be a registry id (e.g. "ai.viral") or a DRS base
        URL. Set ``expand=true`` to expand bundle contents. Returns a clear
        auth_required error (with the WWW-Authenticate challenge) if the object
        is protected.
        """
        c = ctx()
        resolved = await c.resolve(service_id_or_url, "drs")
        data = await c.drs.get_object(resolved.url, object_id, expand=expand)
        return ok(data)

    @mcp.tool(annotations=READ_REMOTE)
    @safe_tool
    async def drs_get_access_url(service_id_or_url: str, object_id: str,
                                 access_id: str | None = None) -> dict:
        """Resolve a concrete, downloadable access URL for a DRS object.

        If ``access_id`` is omitted, the object's access methods are inspected and
        a suitable one is chosen (preferring an inline access_url, else resolving
        via the /access/{access_id} endpoint).
        """
        c = ctx()
        resolved = await c.resolve(service_id_or_url, "drs")
        data = await c.drs.get_access_url(resolved.url, object_id, access_id)
        return ok(data)

    @mcp.tool(annotations=READ_REMOTE)
    @safe_tool
    async def drs_resolve_curie(curie: str, expand: bool = False) -> dict:
        """Resolve a DRS CURIE (``prefix:accession``) using the registry's curiePrefix map.

        Looks up which registered DRS service owns ``prefix`` and fetches the
        object ``accession`` from it. Falls back to an informative error listing
        known prefixes if the prefix is unknown.
        """
        c = ctx()
        if ":" not in curie:
            raise ToolError("bad_input", "curie must be of the form 'prefix:accession'")
        prefix, accession = curie.split(":", 1)
        services = await c.registry.services()
        known = {}
        target = None
        for svc in services:
            cp = svc.curiePrefix
            if cp and cp not in ("N/A", "TBC"):
                known[cp] = svc.id
                if cp.lower() == prefix.lower():
                    target = svc
        if not target:
            raise ToolError(ERR_NOT_FOUND,
                            f"unknown CURIE prefix '{prefix}'", known_prefixes=known)
        data = await c.drs.get_object(target.url, accession, expand=expand)
        return ok(data, resolved_service=target.id, prefix=prefix, accession=accession)
