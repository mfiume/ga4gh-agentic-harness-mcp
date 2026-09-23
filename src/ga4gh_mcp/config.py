"""Runtime configuration for the GA4GH MCP service.

All settings are read from environment variables prefixed with ``GA4GH_MCP_``
(e.g. ``GA4GH_MCP_TRANSPORT=http``) with sensible defaults so the server runs
with zero configuration against the public registry.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_REGISTRY_URL = "https://registry.ga4gh.org/v1"


def config_home() -> Path:
    """Directory for persisted config + token cache (``~/.ga4gh-mcp`` by default)."""
    return Path(os.environ.get("GA4GH_MCP_HOME", str(Path.home() / ".ga4gh-mcp")))


class Settings(BaseSettings):
    """Server settings. Instantiate via :func:`load_settings`."""

    model_config = SettingsConfigDict(env_prefix="GA4GH_MCP_", extra="ignore")

    # Transport: "stdio" (Claude Desktop / Claude Code) or "http" (streamable HTTP for
    # remote clients, Vertex, Bedrock). "sse" is also accepted (legacy).
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8080

    # Registry + HTTP behaviour
    registry_url: str = DEFAULT_REGISTRY_URL
    request_timeout: float = 15.0
    probe_timeout: float = 8.0
    max_retries: int = 2
    cache_ttl: float = 300.0

    log_level: str = "INFO"

    # Refuse outbound requests (and redirect hops) to loopback, private, link-local (cloud
    # metadata, 169.254.169.254) and other non-global addresses. None => on for the HTTP/SSE
    # transports (hosted), off for stdio, where the caller already owns the network.
    block_private_addresses: bool | None = None

    def blocks_private_addresses(self) -> bool:
        if self.block_private_addresses is not None:
            return self.block_private_addresses
        return self.normalized_transport() != "stdio"

    # Auth: a global bearer token, sent only to the hosts listed in ``bearer_hosts``
    # (GA4GH_MCP_BEARER_HOSTS, comma-separated host or host:port). Tools accept raw URLs
    # from the model, so an empty allow-list means the global token is never sent.
    bearer_token: str | None = Field(default=None, repr=False)
    bearer_hosts: str = ""

    def bearer_host_set(self) -> set[str]:
        return {h.strip().lower() for h in self.bearer_hosts.split(",") if h.strip()}

    # Optional path to a YAML auth config (per-host tokens / OAuth clients).
    # Defaults to <config_home>/config.yaml when present.
    config_file: str | None = None

    def resolved_config_file(self) -> Path | None:
        if self.config_file:
            p = Path(self.config_file).expanduser()
            return p if p.exists() else None
        default = config_home() / "config.yaml"
        return default if default.exists() else None

    def normalized_transport(self) -> str:
        t = (self.transport or "stdio").lower().strip()
        if t in ("http", "streamable-http", "streamable_http", "streamablehttp"):
            return "streamable-http"
        if t == "sse":
            return "sse"
        return "stdio"


def load_settings() -> Settings:
    return Settings()
