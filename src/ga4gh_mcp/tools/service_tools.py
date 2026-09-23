"""Generic, type-agnostic tools that work against *any* GA4GH service via
service-info and an authenticated request passthrough."""

from __future__ import annotations

from ..annotations import READ_LOCAL, READ_REMOTE, annotations
from ..context import ctx
from ..errors import ERR_UPSTREAM, err, ok, safe_tool
from ..serviceinfo import fetch_service_info


def register(mcp) -> None:
    @mcp.tool(annotations=READ_REMOTE)
    @safe_tool
    async def service_get_info(service_id_or_url: str, artifact: str | None = None) -> dict:
        """Fetch a GA4GH service-info document for any service (registry id or URL).

        Tolerant of inconsistent base URLs and older layouts: tries multiple
        candidate endpoints, reconciles the registry's declared version against
        the version the service reports, and returns clear warnings when they
        differ or when the service is non-compliant/unreachable.
        """
        c = ctx()
        resolved = await c.resolve(service_id_or_url, artifact)
        headers = await c.auth.resolve_headers(resolved.url)
        info = await fetch_service_info(c.http, resolved.url, resolved.artifact,
                                        auth_headers=headers or None,
                                        timeout=c.settings.probe_timeout)
        warnings = list(info.get("warnings", []))
        declared = None
        if resolved.service and resolved.service.type:
            declared = resolved.service.type.version
        reported = info.get("reported_version")
        if declared and reported and str(declared)[:3] != str(reported)[:3]:
            warnings.append(f"registry declares version '{declared}' but service reports '{reported}'")

        payload = {
            "service_id": resolved.service.id if resolved.service else None,
            "resolved_from": resolved.source,
            "url": resolved.url,
            "artifact": resolved.artifact,
            "declared_version": declared,
            "found": info["found"],
            "service_info_endpoint": info.get("endpoint"),
            "reported_artifact": info.get("reported_type"),
            "reported_version": reported,
            "service_info": info.get("service_info"),
            "auth_challenge": info.get("auth_challenge"),
            "attempts": info.get("attempts"),
        }
        if not info["found"]:
            return err(ERR_UPSTREAM, "could not retrieve service-info", warnings=warnings or None,
                       **{k: v for k, v in payload.items() if k != "service_info"})
        return ok(payload, warnings=warnings or None)

    write_ok = ctx().settings.allow_write_methods

    @mcp.tool(annotations=(annotations(read_only=False, destructive=True, idempotent=False)
                           if write_ok else READ_REMOTE))
    @safe_tool
    async def service_request(
        service_id_or_url: str,
        path: str,
        method: str = "GET",
        query: dict | None = None,
        body: dict | None = None,
        artifact: str | None = None,
    ) -> dict:
        """Make an authenticated request to an arbitrary path on a GA4GH service.

        A power tool for endpoints without a specialized wrapper. ``path`` is
        appended to the service's normalized base URL (e.g. "/objects/{id}" for
        DRS, "/tools" for TRS). Auth headers are attached automatically based on
        configured/cached credentials for the host. GET only, unless the operator set
        GA4GH_MCP_ALLOW_WRITE_METHODS=true, which also allows POST.
        """
        c = ctx()
        method = method.upper()
        if method not in ("GET", "POST"):
            return err("bad_input", "only GET and POST are supported")
        if method != "GET" and not c.settings.allow_write_methods:
            return err("bad_input",
                       f"{method} is disabled: service_request is read-only (GET) by default. "
                       "State-changing requests (e.g. WES/TES submit or cancel) must be enabled "
                       "by the server operator with GA4GH_MCP_ALLOW_WRITE_METHODS=true.")
        resolved = await c.resolve(service_id_or_url, artifact)
        from ..normalize import normalize_base_url

        base = normalize_base_url(resolved.url, resolved.artifact)
        if not path.startswith("/"):
            path = "/" + path
        full = f"{base}{path}"
        headers = await c.auth.resolve_headers(full)
        res = await c.http.request(method, full, params=query, json=body, headers=headers or None)
        payload = {
            "request": {"method": method, "url": full, "query": query},
            "status": res.status,
            "ok": res.ok,
            "json": res.json,
            "text": None if res.json is not None else (res.text or "")[:4000],
            "auth_challenge": None,
        }
        if res.status in (401, 403):
            from ..normalize import parse_www_authenticate
            payload["auth_challenge"] = parse_www_authenticate(res.www_authenticate) or None
            return err("auth_required", f"authentication required (HTTP {res.status})", **payload)
        if not res.ok:
            return err(ERR_UPSTREAM, f"upstream returned HTTP {res.status}"
                       if res.status else (res.error or "request failed"), **payload)
        return ok(payload)

    @mcp.tool(annotations=READ_LOCAL)
    @safe_tool
    async def list_supported_service_types() -> dict:
        """List the GA4GH service types this server understands, their specs, and which
        have specialized tools vs. generic (service-info) handling."""
        from ..ga4gh import plugins

        return ok([
            {
                "artifact": p.artifact,
                "title": p.title,
                "spec_url": p.spec_url,
                "docs_url": p.docs_url,
                "api_prefix": p.api_prefix,
                "capabilities": list(p.capabilities),
                "specialized_tools": list(p.tools),
                "notes": p.notes,
            }
            for p in plugins.all_plugins()
        ])
