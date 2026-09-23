"""Auth management tools: inspect, discover, set tokens, and interactive login."""

from __future__ import annotations

import asyncio

from ..annotations import READ_LOCAL, READ_REMOTE, annotations
from ..auth.manager import AuthError, host_env_slug
from ..context import ctx
from ..errors import err, ok, safe_tool
from ..normalize import host_of

# Keep references to background polling tasks so they aren't garbage-collected.
_BG_TASKS: set[asyncio.Task] = set()


def register(mcp) -> None:
    @mcp.tool(annotations=READ_LOCAL)
    @safe_tool
    async def auth_status(service_id_or_url: str | None = None) -> dict:
        """Show authentication status: which hosts have static/OAuth tokens configured,
        token sources, validity, and whether a global bearer is set. Pass a service id
        or URL to scope to one host."""
        c = ctx()
        url = None
        if service_id_or_url:
            try:
                resolved = await c.resolve(service_id_or_url)
                url = resolved.url
            except Exception:  # noqa: BLE001
                url = service_id_or_url
        return ok(c.auth.status(url))

    @mcp.tool(annotations=READ_REMOTE)
    @safe_tool
    async def auth_discover(service_id_or_url: str, artifact: str | None = None) -> dict:
        """Discover what authentication a service requires and how to obtain it.

        Probes a protected endpoint for a WWW-Authenticate challenge and performs
        OIDC discovery, reporting the available OAuth flows (device_code /
        client_credentials / authorization_code), endpoints, and next steps.
        """
        c = ctx()
        resolved = await c.resolve(service_id_or_url, artifact)
        result = await c.auth.discover(resolved.url, resolved.artifact)
        result["host"] = host_of(resolved.url)
        result["env_token_var"] = f"GA4GH_MCP_TOKEN_{host_env_slug(host_of(resolved.url))}"
        return ok(result)

    @mcp.tool(annotations=annotations(read_only=False, open_world=False))
    @safe_tool
    async def auth_set_token(service_id_or_url: str, token: str) -> dict:
        """Set a static bearer token for a service's host for this session.

        Use this to supply a token you already hold (e.g. from a portal). The
        token is scoped to that host only and never persisted to disk by this tool.
        """
        c = ctx()
        try:
            resolved = await c.resolve(service_id_or_url)
            target = resolved.url
        except Exception:  # noqa: BLE001
            target = service_id_or_url
        host = c.auth.set_static_token(target, token)
        return ok({"host": host, "message": f"static bearer token set for {host} (session only)"})

    @mcp.tool(annotations=annotations(read_only=False, idempotent=False))
    @safe_tool
    async def auth_login(service_id_or_url: str, artifact: str | None = None) -> dict:
        """Begin an OAuth 2.0 device-code login for a service (interactive).

        Returns a verification URL and user code to open in a browser. Polling
        runs in the background and the resulting token is cached for the host;
        check auth_status to confirm completion. Requires a registered client_id
        (env GA4GH_MCP_CLIENT_ID_<HOST> or YAML config). For fully headless login,
        use the CLI: `ga4gh-mcp auth login <service>`.
        """
        c = ctx()
        resolved = await c.resolve(service_id_or_url, artifact)
        try:
            begin = await c.auth.begin_device_code(resolved.url, resolved.artifact)
        except AuthError as e:
            return err("auth_required", str(e))

        async def poll():
            try:
                await c.auth.poll_device_code(
                    resolved.url, begin["device_code"],
                    interval=begin.get("interval", 5),
                    timeout=float(begin.get("expires_in", 300) or 300),
                    artifact=resolved.artifact,
                )
            except Exception:  # noqa: BLE001
                pass

        task = asyncio.create_task(poll())
        _BG_TASKS.add(task)
        task.add_done_callback(_BG_TASKS.discard)

        return ok({
            "host": begin["host"],
            "verification_uri": begin.get("verification_uri"),
            "verification_uri_complete": begin.get("verification_uri_complete"),
            "user_code": begin.get("user_code"),
            "expires_in": begin.get("expires_in"),
            "message": "Open the verification URL and enter the user code. Polling in background; "
                       "call auth_status to confirm.",
        })

    @mcp.tool(annotations=annotations(read_only=False, destructive=True, open_world=False))
    @safe_tool
    async def auth_revoke(service_id_or_url: str | None = None) -> dict:
        """Revoke cached tokens for a host (or all hosts if omitted)."""
        c = ctx()
        target = None
        if service_id_or_url:
            try:
                resolved = await c.resolve(service_id_or_url)
                target = resolved.url
            except Exception:  # noqa: BLE001
                target = service_id_or_url
        revoked = c.auth.revoke(target)
        return ok({"revoked_hosts": revoked})
