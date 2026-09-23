"""Shared application context — the wiring that tools operate against.

A single :class:`AppContext` is built at startup and referenced by all tools.
"""

from __future__ import annotations

from dataclasses import dataclass

from .auth.manager import AuthManager
from .auth.store import TokenStore
from .cache import TTLCache
from .config import Settings
from .errors import ERR_BAD_INPUT, ERR_NOT_FOUND, ToolError
from .ga4gh.drs import DRSClient
from .ga4gh.trs import TRSClient
from .ga4gh.wes import WESClient
from .http import AsyncHttp
from .models import Service
from .normalize import host_of
from .registry import RegistryClient


@dataclass
class ResolvedService:
    url: str
    artifact: str | None
    service: Service | None
    source: str  # "registry" | "url"


class AppContext:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.http = AsyncHttp(
            timeout=settings.request_timeout,
            max_retries=settings.max_retries,
            block_private_addresses=settings.blocks_private_addresses(),
        )
        self.cache = TTLCache(ttl=settings.cache_ttl)
        self.registry = RegistryClient(settings.registry_url, self.http, self.cache)
        self.auth = AuthManager(settings, self.http, TokenStore())
        self.drs = DRSClient(self.http, self.auth, timeout=settings.request_timeout)
        self.trs = TRSClient(self.http, self.auth, timeout=settings.request_timeout)
        self.wes = WESClient(self.http, self.auth, timeout=settings.request_timeout)

    async def resolve(self, service_id_or_url: str, artifact: str | None = None) -> ResolvedService:
        """Turn a registry service id *or* a raw URL into a concrete endpoint."""
        s = (service_id_or_url or "").strip()
        if not s:
            raise ToolError(ERR_BAD_INPUT, "service_id_or_url is required")
        if s.startswith("http://") or s.startswith("https://"):
            # Raw URL — try to enrich with registry metadata by host match.
            svc = None
            try:
                matches = await self.registry.find_services(url=s)
                if not matches:
                    matches = await self.registry.find_services(host=host_of(s))
                svc = matches[0] if matches else None
            except Exception:  # noqa: BLE001 — registry optional for raw URLs
                svc = None
            return ResolvedService(url=s, artifact=artifact or (svc.artifact if svc else None),
                                   service=svc, source="url")
        # Treat as a registry service id.
        svc = await self.registry.get_service(s)
        if not svc:
            raise ToolError(ERR_NOT_FOUND,
                            f"no registered service with id '{s}'. Pass a full URL, or use "
                            f"registry_list_services to find valid ids.")
        if not svc.url:
            raise ToolError(ERR_BAD_INPUT, f"service '{s}' has no URL registered")
        return ResolvedService(url=svc.url, artifact=artifact or svc.artifact,
                               service=svc, source="registry")

    async def aclose(self) -> None:
        await self.http.close()


# Module-level singleton, set by server.build_server().
_CTX: AppContext | None = None


def set_context(ctx: AppContext) -> None:
    global _CTX
    _CTX = ctx


def ctx() -> AppContext:
    if _CTX is None:
        raise RuntimeError("AppContext not initialized")
    return _CTX
