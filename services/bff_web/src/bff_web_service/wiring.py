"""Composition root bff_web: httpx-клиенты источников → запросы экранов → API.

Модуль вне слоёв (как ``main``): только он знает и инфраструктуру, и API.
"""

from __future__ import annotations

import httpx

from bff_web_service.api.deps import BffDeps
from bff_web_service.application.ports import PageCache
from bff_web_service.application.queries import (
    GetHomePage,
    GetResultsPage,
    GetSession,
    GetSoloPage,
    PageLayout,
)
from bff_web_service.infrastructure.cache import RedisPageCache
from bff_web_service.infrastructure.clients import (
    HttpCatalogClient,
    HttpGameClient,
    HttpIdentityClient,
)
from bff_web_service.infrastructure.http import CircuitBreaker, UpstreamHttp
from bff_web_service.settings import Settings
from gv_service import Infrastructure


def build_deps(infra: Infrastructure) -> BffDeps:
    """Собрать клиенты источников (по одному пулу и breaker на сервис) и запросы."""
    settings = infra.settings
    if not isinstance(settings, Settings):
        raise TypeError("bff_web ожидает bff_web_service.settings.Settings")
    up, pc = settings.upstreams, settings.page_cache
    cache: PageCache | None = (
        RedisPageCache(infra.redis, timeout_s=pc.op_timeout_s) if infra.redis is not None else None
    )

    def upstream(service: str, base_url: str) -> UpstreamHttp:
        client = httpx.AsyncClient(
            base_url=base_url,
            timeout=up.timeout_s,
            limits=httpx.Limits(max_connections=up.max_connections),
            # Редиректы источника — не наш контракт: следовать им значило бы унести
            # Bearer пользователя по чужому адресу.
            follow_redirects=False,
        )
        infra.add_closer(client.aclose)
        breaker = CircuitBreaker(failures=up.breaker_failures, reset_s=up.breaker_reset_s)
        return UpstreamHttp(service, client, breaker, timeout_s=up.timeout_s, cache=cache)

    game = HttpGameClient(
        upstream("game", up.game_url), modes_ttl_s=pc.modes_ttl_s, cache_prefix=pc.key_prefix
    )
    catalog = HttpCatalogClient(
        upstream("catalog", up.catalog_url), item_ttl_s=pc.item_ttl_s, cache_prefix=pc.key_prefix
    )
    identity = HttpIdentityClient(upstream("identity", up.identity_url))
    pages = settings.pages
    layout = PageLayout.of(
        random_source_types=pages.random_source_types,
        scheduled_source_types=pages.scheduled_source_types,
        weekly_source_types=pages.weekly_source_types,
    )
    return BffDeps(
        get_session=GetSession(identity),
        get_home=GetHomePage(game, concurrency=up.max_concurrency, layout=layout),
        get_solo=GetSoloPage(game, concurrency=up.max_concurrency, layout=layout),
        get_results=GetResultsPage(game, catalog, concurrency=up.max_concurrency),
        http_cache=settings.http_cache,
    )
