"""Pluggable auth layer for heterogeneous GA4GH services.

Resolution order for outgoing requests (per host):

1. A valid cached **OAuth** token (device-code / client-credentials), refreshed
   if near expiry.
2. A **static bearer** token configured for that host (env ``GA4GH_MCP_TOKEN_<HOST>``,
   YAML ``hosts.<host>.token``, or a runtime ``auth_set_token``).
3. The **global** bearer token (``GA4GH_MCP_BEARER_TOKEN``), only for hosts in
   ``GA4GH_MCP_BEARER_HOSTS``.
4. No auth (public).

Tokens are only ever sent to the host they are scoped to, and only over https
(plain http is tolerated for loopback development servers). Tools accept raw URLs
chosen by the model, so a token must never follow a URL to a host it was not
configured for.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from ..config import Settings
from ..http import AsyncHttp
from ..normalize import host_of
from .discovery import discover_auth_requirement, discover_oidc
from .store import TokenStore

logger = logging.getLogger("ga4gh_mcp.auth")

DEVICE_CODE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


_LOOPBACK = {"localhost", "127.0.0.1", "::1"}


def _may_carry_credentials(url: str) -> bool:
    """Credentials travel only over TLS (plain http is tolerated for loopback dev servers)."""
    p = urlparse(url if "://" in url else f"https://{url}")
    scheme = (p.scheme or "").lower()
    return scheme == "https" or (scheme == "http" and (p.hostname or "").lower() in _LOOPBACK)


def host_env_slug(host: str) -> str:
    """``data.terra.bio`` -> ``DATA_TERRA_BIO`` for env var lookup."""
    return re.sub(r"[^A-Za-z0-9]", "_", host).upper()


@dataclass
class HostAuth:
    host: str
    token: str | None = None
    token_endpoint: str | None = None
    device_authorization_endpoint: str | None = None
    authorization_endpoint: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    scope: str | None = None
    grant: str | None = None
    issuer: str | None = None
    resource: str | None = None
    # True when the token/device endpoints came from operator config (explicit endpoints, or
    # discovery against a configured ``issuer``). False when they were discovered from the
    # service's own response (WWW-Authenticate realm, or its origin's .well-known), which the
    # service controls: those endpoints never receive ``client_secret``.
    endpoints_configured: bool = False
    extra: dict = field(default_factory=dict)


class AuthError(Exception):
    pass


class AuthManager:
    def __init__(self, settings: Settings, http: AsyncHttp, store: TokenStore | None = None) -> None:
        self._settings = settings
        self._http = http
        self._store = store or TokenStore()
        self._session_tokens: dict[str, str] = {}  # host -> static token set at runtime
        self._yaml_hosts: dict[str, dict] = self._load_yaml()

    # ---- configuration ----------------------------------------------------

    def _load_yaml(self) -> dict[str, dict]:
        path = self._settings.resolved_config_file()
        if not path:
            return {}
        try:
            import yaml

            data = yaml.safe_load(path.read_text()) or {}
            hosts = data.get("hosts", {}) or {}
            return {h.lower(): (cfg or {}) for h, cfg in hosts.items()}
        except Exception as e:  # noqa: BLE001
            logger.warning("failed to load auth config %s: %s", path, e)
            return {}

    def host_config(self, host: str) -> HostAuth:
        host = host.lower()
        ha = HostAuth(host=host)
        cfg = self._yaml_hosts.get(host, {})
        if cfg:
            ha.token = cfg.get("token")
            oauth = cfg.get("oauth", {}) or {}
            ha.token_endpoint = oauth.get("token_endpoint")
            ha.device_authorization_endpoint = oauth.get("device_authorization_endpoint")
            ha.authorization_endpoint = oauth.get("authorization_endpoint")
            ha.client_id = oauth.get("client_id")
            ha.client_secret = oauth.get("client_secret")
            ha.scope = oauth.get("scope")
            ha.grant = oauth.get("grant")
            ha.issuer = oauth.get("issuer")
            ha.resource = oauth.get("resource")
        # Env per-host token overrides YAML token
        env_token = os.environ.get(f"GA4GH_MCP_TOKEN_{host_env_slug(host)}")
        if env_token:
            ha.token = env_token
        # Env per-host client_id / secret
        env_cid = os.environ.get(f"GA4GH_MCP_CLIENT_ID_{host_env_slug(host)}")
        env_secret = os.environ.get(f"GA4GH_MCP_CLIENT_SECRET_{host_env_slug(host)}")
        if env_cid:
            ha.client_id = env_cid
        if env_secret:
            ha.client_secret = env_secret
        return ha

    # ---- header resolution ------------------------------------------------

    async def resolve_headers(self, url: str) -> dict[str, str]:
        host = host_of(url)
        if not host or not _may_carry_credentials(url):
            return {}
        # 1) cached OAuth token (refresh if needed)
        token = await self._valid_oauth_token(host)
        if token:
            return {"Authorization": f"Bearer {token}"}
        # 2) static token (session > env/yaml)
        if host in self._session_tokens:
            return {"Authorization": f"Bearer {self._session_tokens[host]}"}
        ha = self.host_config(host)
        if ha.token:
            return {"Authorization": f"Bearer {ha.token}"}
        # 3) global bearer, allow-listed hosts only
        if self._settings.bearer_token and host in self._settings.bearer_host_set():
            return {"Authorization": f"Bearer {self._settings.bearer_token}"}
        return {}

    async def _valid_oauth_token(self, host: str) -> str | None:
        tok = self._store.valid_access_token(host)
        if tok:
            return tok
        entry = self._store.get(host)
        if entry and entry.get("refresh_token"):
            try:
                return await self._refresh(host, entry)
            except Exception as e:  # noqa: BLE001
                logger.info("refresh for %s failed: %s", host, e)
        return None

    # ---- flows ------------------------------------------------------------

    async def _endpoints_for(self, url: str, artifact: str | None = None) -> HostAuth:
        """Fill in OAuth endpoints from config, then OIDC discovery."""
        host = host_of(url)
        ha = self.host_config(host)
        if ha.token_endpoint:
            ha.endpoints_configured = True
            return ha
        # discover
        from urllib.parse import urlparse

        from ..normalize import normalize_base_url

        base = normalize_base_url(url, artifact)
        parsed = urlparse(base if "://" in base else f"https://{base}")
        origin = f"{parsed.scheme}://{parsed.netloc}"
        oidc = None
        if ha.issuer:
            oidc = await discover_oidc(self._http, ha.issuer)
            ha.endpoints_configured = bool(oidc)
        if not oidc:
            req = await discover_auth_requirement(self._http, url, artifact)
            oidc = req.get("oidc")
        if not oidc:
            oidc = await discover_oidc(self._http, origin)
        if oidc:
            ha.token_endpoint = ha.token_endpoint or oidc.get("token_endpoint")
            ha.device_authorization_endpoint = ha.device_authorization_endpoint or oidc.get(
                "device_authorization_endpoint")
            ha.authorization_endpoint = ha.authorization_endpoint or oidc.get("authorization_endpoint")
            ha.issuer = ha.issuer or oidc.get("issuer")
        return ha

    async def begin_device_code(self, url: str, artifact: str | None = None) -> dict:
        """Start an OAuth 2.0 device-code flow. Returns the user prompt details."""
        ha = await self._endpoints_for(url, artifact)
        if not ha.client_id:
            raise AuthError(
                f"No client_id configured for {ha.host}. Set GA4GH_MCP_CLIENT_ID_"
                f"{host_env_slug(ha.host)} or add it to the YAML config."
            )
        if not ha.device_authorization_endpoint:
            raise AuthError(f"No device_authorization_endpoint discovered for {ha.host}.")
        data = {"client_id": ha.client_id, "scope": ha.scope or "openid offline_access"}
        if ha.client_secret and ha.endpoints_configured:
            data["client_secret"] = ha.client_secret
        if ha.resource:
            data["resource"] = ha.resource
        res = await self._http.request("POST", ha.device_authorization_endpoint, data=data)
        if res.status != 200 or not isinstance(res.json, dict):
            raise AuthError(f"device authorization request failed: {res.error or res.status}")
        d = res.json
        return {
            "host": ha.host,
            "verification_uri": d.get("verification_uri") or d.get("verification_url"),
            "verification_uri_complete": d.get("verification_uri_complete"),
            "user_code": d.get("user_code"),
            "device_code": d.get("device_code"),
            "expires_in": d.get("expires_in"),
            "interval": d.get("interval", 5),
            "token_endpoint": ha.token_endpoint,
            "client_id": ha.client_id,
        }

    async def poll_device_code(self, url: str, device_code: str, *, interval: int = 5,
                               timeout: float = 300.0, artifact: str | None = None) -> dict:
        """Poll the token endpoint until the user authorizes (blocking, CLI-friendly)."""
        ha = await self._endpoints_for(url, artifact)
        data = {"grant_type": DEVICE_CODE_GRANT, "device_code": device_code, "client_id": ha.client_id}
        if ha.client_secret and ha.endpoints_configured:
            data["client_secret"] = ha.client_secret
        deadline = time.monotonic() + timeout
        wait = interval
        while time.monotonic() < deadline:
            res = await self._http.request("POST", ha.token_endpoint, data=data)
            if res.status == 200 and isinstance(res.json, dict):
                return self._persist_token(ha.host, res.json, source="device_code")
            body = res.json if isinstance(res.json, dict) else {}
            error = body.get("error", "")
            if error == "authorization_pending":
                await asyncio.sleep(wait)
            elif error == "slow_down":
                wait += 5
                await asyncio.sleep(wait)
            elif error in ("expired_token", "access_denied"):
                raise AuthError(f"device-code flow failed: {error}")
            else:
                raise AuthError(f"token request failed: {error or res.error or res.status}")
        raise AuthError("timed out waiting for user authorization")

    async def client_credentials(self, url: str, artifact: str | None = None) -> dict:
        """OAuth 2.0 client-credentials flow (machine-to-machine)."""
        ha = await self._endpoints_for(url, artifact)
        if not (ha.client_id and ha.client_secret and ha.token_endpoint):
            raise AuthError(
                f"client-credentials requires client_id, client_secret and token_endpoint for {ha.host}"
            )
        if not ha.endpoints_configured:
            raise AuthError(
                f"refusing to send client_secret for {ha.host} to a token endpoint discovered from "
                f"the service's own response ({ha.token_endpoint}); configure "
                f"hosts.{ha.host}.oauth.token_endpoint or .issuer in the YAML config"
            )
        data = {
            "grant_type": "client_credentials",
            "client_id": ha.client_id,
            "client_secret": ha.client_secret,
        }
        if ha.scope:
            data["scope"] = ha.scope
        if ha.resource:
            data["resource"] = ha.resource
        res = await self._http.request("POST", ha.token_endpoint, data=data)
        if res.status != 200 or not isinstance(res.json, dict):
            raise AuthError(f"client-credentials token request failed: {res.error or res.status}")
        return self._persist_token(ha.host, res.json, source="client_credentials")

    async def _refresh(self, host: str, entry: dict) -> str | None:
        ha = self.host_config(host)
        if not ha.token_endpoint:
            # Try discovery using the issuer stored with the token, if any
            issuer = entry.get("issuer")
            if issuer:
                oidc = await discover_oidc(self._http, issuer)
                if oidc:
                    ha.token_endpoint = oidc.get("token_endpoint")
        if not ha.token_endpoint:
            return None
        data = {
            "grant_type": "refresh_token",
            "refresh_token": entry["refresh_token"],
            "client_id": ha.client_id or entry.get("client_id", ""),
        }
        if ha.client_secret:
            data["client_secret"] = ha.client_secret
        res = await self._http.request("POST", ha.token_endpoint, data=data)
        if res.status == 200 and isinstance(res.json, dict):
            self._persist_token(host, res.json, source=entry.get("source", "oauth"),
                                 fallback_refresh=entry.get("refresh_token"))
            return res.json.get("access_token")
        return None

    def _persist_token(self, host: str, token_data: dict, *, source: str,
                       fallback_refresh: str | None = None) -> dict:
        access = token_data.get("access_token")
        if not access:
            raise AuthError("token response missing access_token")
        expires_in = token_data.get("expires_in")
        expires_at = time.time() + float(expires_in) if expires_in else None
        refresh = token_data.get("refresh_token") or fallback_refresh
        self._store.set(host, access_token=access, refresh_token=refresh,
                        expires_at=expires_at, source=source)
        return {
            "host": host,
            "source": source,
            "expires_at": expires_at,
            "has_refresh_token": bool(refresh),
        }

    # ---- runtime management ----------------------------------------------

    def set_static_token(self, url_or_host: str, token: str) -> str:
        host = host_of(url_or_host) or url_or_host.lower()
        self._session_tokens[host] = token
        return host

    def revoke(self, url_or_host: str | None = None) -> list[str]:
        if url_or_host is None:
            revoked = list(set(self._session_tokens) | set(self._store.hosts()))
            self._session_tokens.clear()
            self._store.delete(None)
            return revoked
        host = host_of(url_or_host) or url_or_host.lower()
        self._session_tokens.pop(host, None)
        self._store.delete(host)
        return [host]

    def status(self, url_or_host: str | None = None) -> dict:
        def host_status(host: str) -> dict:
            ha = self.host_config(host)
            oauth_entry = self._store.get(host)
            return {
                "host": host,
                "has_session_token": host in self._session_tokens,
                "has_static_token": bool(ha.token),
                "has_oauth_token": bool(oauth_entry),
                "oauth_source": (oauth_entry or {}).get("source"),
                "oauth_expires_at": (oauth_entry or {}).get("expires_at"),
                "oauth_valid": bool(self._store.valid_access_token(host)),
                "client_id_configured": bool(ha.client_id),
            }

        if url_or_host:
            host = host_of(url_or_host) or url_or_host.lower()
            return host_status(host)
        hosts = sorted(set(self._session_tokens) | set(self._store.hosts()) | set(self._yaml_hosts))
        return {
            "global_bearer_configured": bool(self._settings.bearer_token),
            "global_bearer_hosts": sorted(self._settings.bearer_host_set()),
            "config_file": str(self._settings.resolved_config_file() or ""),
            "hosts": [host_status(h) for h in hosts],
        }

    async def discover(self, url: str, artifact: str | None = None) -> dict:
        return await discover_auth_requirement(self._http, url, artifact)
