"""MCP tools covering the Implementation Registry surface: list / details / search /
types / implementations / service-info / health."""

from __future__ import annotations

import asyncio

from ..annotations import READ_REMOTE
from ..context import ctx
from ..errors import ERR_NOT_FOUND, ToolError, err, ok, safe_tool
from ..normalize import host_of, normalize_environment, parse_version
from ..serviceinfo import probe_liveness


def register(mcp) -> None:
    @mcp.tool(annotations=READ_REMOTE)
    @safe_tool
    async def registry_list_services(
        artifact: str | None = None,
        organization: str | None = None,
        version: str | None = None,
        environment: str | None = None,
        limit: int = 50,
        check_liveness: bool = False,
    ) -> dict:
        """List GA4GH web services registered in the Implementation Registry.

        Filter by ``artifact`` (e.g. "drs", "wes", "trs"), ``organization`` (substring),
        ``version`` (declared spec version prefix), and ``environment`` ("production",
        "development", ...). Set ``check_liveness=true`` to additionally probe each
        matched service's service-info concurrently (slower). Returns compact
        summaries; use registry_get_service for full detail.
        """
        c = ctx()
        services = await c.registry.services()
        art = (artifact or "").lower()
        org = (organization or "").lower()
        envf = normalize_environment(environment) if environment else None

        matched = []
        for svc in services:
            if art and (svc.artifact or "").lower() != art:
                continue
            if org:
                oname = ((svc.organization.name if svc.organization else "") or "").lower()
                oid = ((svc.organization.id if svc.organization else "") or "").lower()
                if org not in oname and org not in oid:
                    continue
            if version:
                dv = (svc.type.version if svc.type else "") or ""
                if not dv.startswith(version):
                    continue
            if envf and normalize_environment(svc.environment) != envf:
                continue
            matched.append(svc)

        total = len(matched)
        matched = matched[: max(1, limit)]
        summaries = [svc.summary() for svc in matched]

        if check_liveness:
            sem = asyncio.Semaphore(10)

            async def probe(i, svc):
                async with sem:
                    if not svc.url:
                        return
                    headers = await c.auth.resolve_headers(svc.url)
                    res = await probe_liveness(c.http, svc.url, svc.artifact,
                                               auth_headers=headers or None,
                                               timeout=c.settings.probe_timeout)
                    summaries[i]["liveness"] = res.get("liveness")
                    summaries[i]["reachable"] = res.get("reachable")
                    if res.get("reported_version"):
                        summaries[i]["reported_version"] = res["reported_version"]

            await asyncio.gather(*(probe(i, s) for i, s in enumerate(matched)))

        return ok(summaries, total_matched=total, returned=len(summaries),
                  filters={"artifact": artifact, "organization": organization,
                           "version": version, "environment": environment})

    @mcp.tool(annotations=READ_REMOTE)
    @safe_tool
    async def registry_get_service(service_id: str) -> dict:
        """Get full details for a single registered service by its registry id.

        Includes the normalized base URL and the candidate service-info endpoints
        the server will try (accounting for inconsistent registered URLs).
        """
        c = ctx()
        svc = await c.registry.get_service(service_id)
        if not svc:
            raise ToolError(ERR_NOT_FOUND, f"no service with id '{service_id}'")
        data = svc.summary()
        data["service_info_candidate_urls"] = svc.service_info_urls()
        data["contact_url"] = svc.contactUrl
        data["created_at"] = svc.createdAt
        data["updated_at"] = svc.updatedAt
        data["raw"] = svc.model_dump(exclude_none=True)
        return ok(data)

    @mcp.tool(annotations=READ_REMOTE)
    @safe_tool
    async def registry_search(query: str, kind: str = "all", limit: int = 25) -> dict:
        """Free-text search across registered services and implementations.

        Matches ``query`` (case-insensitive) against id, name, description,
        organization and artifact. ``kind`` is "services", "implementations", or "all".
        """
        c = ctx()
        q = (query or "").strip().lower()
        if not q:
            return err("bad_input", "query is required")

        def matches(summary: dict, extra: str = "") -> bool:
            hay = " ".join(str(summary.get(k, "")) for k in
                           ("id", "name", "description", "organization", "artifact")) + " " + extra
            return q in hay.lower()

        results: dict = {}
        if kind in ("services", "all"):
            svc_hits = [s.summary() for s in await c.registry.services()]
            results["services"] = [s for s in svc_hits if matches(s)][:limit]
        if kind in ("implementations", "all"):
            impl_hits = [i.summary() for i in await c.registry.implementations()]
            results["implementations"] = [i for i in impl_hits if matches(i)][:limit]
        counts = {k: len(v) for k, v in results.items()}
        return ok(results, counts=counts, query=query)

    @mcp.tool(annotations=READ_REMOTE)
    @safe_tool
    async def registry_list_service_types() -> dict:
        """List the distinct GA4GH service types (artifact + version) present in the registry,
        with per-artifact deployment counts and which types have specialized tool support."""
        from ..ga4gh import plugins

        c = ctx()
        types = await c.registry.service_types()
        services = await c.registry.services()
        counts: dict[str, int] = {}
        for s in services:
            a = (s.artifact or "unknown")
            counts[a] = counts.get(a, 0) + 1
        type_list = []
        for t in types:
            plugin = plugins.get(t.artifact)
            type_list.append({
                "group": t.group,
                "artifact": t.artifact,
                "version": t.version,
                "version_parsed": parse_version(t.version),
                "live_deployments": counts.get(t.artifact or "", 0),
                "specialized_tools": list(plugin.tools) if plugin else [],
                "title": plugin.title if plugin else None,
            })
        return ok(type_list, artifact_counts=counts)

    @mcp.tool(annotations=READ_REMOTE)
    @safe_tool
    async def registry_list_implementations(artifact: str | None = None, limit: int = 50) -> dict:
        """List software implementations (codebases) of GA4GH standards from the registry.

        These are reusable open-source implementations, distinct from live deployments.
        """
        c = ctx()
        impls = await c.registry.implementations()
        art = (artifact or "").lower()
        summaries = [i.summary() for i in impls
                     if not art or (i.type.artifact if i.type else "").lower() == art]
        return ok(summaries[:limit], total=len(summaries))

    @mcp.tool(annotations=READ_REMOTE)
    @safe_tool
    async def registry_service_info() -> dict:
        """Return the Implementation Registry's own GA4GH service-info document."""
        c = ctx()
        return ok(await c.registry.service_info())

    @mcp.tool(annotations=READ_REMOTE)
    @safe_tool
    async def registry_check_health(service_id_or_url: str, artifact: str | None = None) -> dict:
        """Live health/liveness check of one registered service (or raw URL).

        Probes service-info, reports reachability, detected spec version, auth
        requirement, and latency — and flags any mismatch between the version the
        registry declares and what the service actually reports.
        """
        c = ctx()
        resolved = await c.resolve(service_id_or_url, artifact)
        headers = await c.auth.resolve_headers(resolved.url)
        probe = await probe_liveness(c.http, resolved.url, resolved.artifact,
                                     auth_headers=headers or None,
                                     timeout=c.settings.probe_timeout)
        warnings = list(probe.get("warnings", []))
        declared = None
        if resolved.service and resolved.service.type:
            declared = resolved.service.type.version
        reported = probe.get("reported_version")
        if declared and reported and not str(reported).startswith(str(declared).split("experimental")[0][:3]):
            warnings.append(
                f"version mismatch: registry declares '{declared}' but service reports '{reported}'")
        return ok({
            "service_id": resolved.service.id if resolved.service else None,
            "url": resolved.url,
            "host": host_of(resolved.url),
            "artifact": resolved.artifact,
            "declared_version": declared,
            **probe,
        }, warnings=warnings or None)
