"""Composition root bff_web: httpx-клиенты источников → запросы экранов → API.

Модуль вне слоёв (как ``main``): только он знает и инфраструктуру, и API.
"""

from __future__ import annotations

import httpx
import structlog

from bff_web_service.api.deps import BffDeps
from bff_web_service.application.ports import PageCache, ServiceTokens
from bff_web_service.application.queries import (
    EventsContext,
    GetCollectionPage,
    GetCollectionsPage,
    GetEventPage,
    GetHomePage,
    GetLeaderboardsPage,
    GetLibraryPage,
    GetLibraryShelfPage,
    GetMatchPage,
    GetOnlinePage,
    GetPlayerPage,
    GetProfilePage,
    GetResultsPage,
    GetRoomPage,
    GetSession,
    GetShellPage,
    GetShopPage,
    GetSoloPage,
    HomeProgress,
    PageLayout,
)
from bff_web_service.infrastructure.cache import RedisPageCache
from bff_web_service.infrastructure.clients import (
    HttpCatalogClient,
    HttpCollectionsClient,
    HttpGameClient,
    HttpIdentityClient,
    HttpLibraryClient,
    HttpMatchClient,
    HttpNotificationsClient,
    HttpProgressionClient,
    HttpRatingClient,
    HttpSocialClient,
)
from bff_web_service.infrastructure.http import CircuitBreaker, UpstreamHttp
from bff_web_service.settings import Settings
from gv_service import Infrastructure, ServiceAuthNotConfiguredError, build_service_token_provider

log = structlog.get_logger(__name__)


def _progression_tokens(infra: Infrastructure) -> ServiceTokens | None:
    """Сервисный токен bff_web → progression (область ``levels.read``); не настроен — ``None``.

    Без токена уровни игроков не заполняются (``level: null``), остальное работает.
    """
    try:
        return build_service_token_provider(infra, "progression")
    except ServiceAuthNotConfiguredError:
        log.warning("bff.service_auth_not_configured", audience="progression")
        return None


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
        upstream("game", up.game_url),
        modes_ttl_s=pc.modes_ttl_s,
        cache_prefix=pc.key_prefix,
        collection_modes_ttl_s=pc.collection_modes_ttl_s,
    )
    catalog = HttpCatalogClient(
        upstream("catalog", up.catalog_url), item_ttl_s=pc.item_ttl_s, cache_prefix=pc.key_prefix
    )
    identity = HttpIdentityClient(upstream("identity", up.identity_url))
    collections = HttpCollectionsClient(
        upstream("collections", up.collections_url),
        catalog_ttl_s=pc.collections_ttl_s,
        cache_prefix=pc.key_prefix,
    )
    library = HttpLibraryClient(upstream("library", up.library_url))
    match = HttpMatchClient(upstream("match", up.match_url))
    rating = HttpRatingClient(
        upstream("rating", up.rating_url), ttl_s=pc.leaderboard_ttl_s, cache_prefix=pc.key_prefix
    )
    progression = HttpProgressionClient(
        upstream("progression", up.progression_url),
        service_tokens=_progression_tokens(infra),
        active_event_ttl_s=pc.active_event_ttl_s,
        event_progress_ttl_s=pc.event_progress_ttl_s,
        cache_prefix=pc.key_prefix,
    )
    notifications = HttpNotificationsClient(upstream("notifications", up.notifications_url))
    social = HttpSocialClient(
        upstream("social", up.social_url),
        online_count_ttl_s=pc.online_count_ttl_s,
        cache_prefix=pc.key_prefix,
    )
    pages = settings.pages
    events = EventsContext(
        progression, default_theme_key=pages.default_theme_key, concurrency=up.max_concurrency
    )
    layout = PageLayout.of(
        random_source_types=pages.random_source_types,
        scheduled_source_types=pages.scheduled_source_types,
        weekly_source_types=pages.weekly_source_types,
        collection_source_types=pages.collection_source_types,
    )
    return BffDeps(
        get_session=GetSession(identity),
        get_home=GetHomePage(
            game,
            concurrency=up.max_concurrency,
            layout=layout,
            events=events,
            progress=HomeProgress(
                progression,
                concurrency=up.max_concurrency,
                deadline_s=pages.home_progress_deadline_s,
            ),
        ),
        get_event_page=GetEventPage(progression, events),
        get_profile=GetProfilePage(progression, concurrency=up.max_concurrency),
        get_player=GetPlayerPage(progression, concurrency=up.max_concurrency, social=social),
        get_shop=GetShopPage(progression, concurrency=up.max_concurrency),
        get_shell=GetShellPage(
            notifications,
            social=social,
            theme_key=pages.default_theme_key,
            events=events,
            concurrency=up.max_concurrency,
        ),
        get_solo=GetSoloPage(game, concurrency=up.max_concurrency, layout=layout),
        get_results=GetResultsPage(game, catalog, concurrency=up.max_concurrency),
        get_collections=GetCollectionsPage(
            collections,
            concurrency=up.max_concurrency,
            showcase_limit=pages.collections_showcase_limit,
            results_limit=pages.collections_results_limit,
        ),
        get_collection=GetCollectionPage(
            collections,
            game,
            social=social,
            concurrency=up.max_concurrency,
            comments_limit=pages.comments_limit,
        ),
        get_library=GetLibraryPage(library, concurrency=up.max_concurrency),
        get_library_shelf=GetLibraryShelfPage(
            library, concurrency=up.max_concurrency, items_limit=pages.library_items_limit
        ),
        get_online=GetOnlinePage(
            match,
            game,
            rating,
            social=social,
            concurrency=up.max_concurrency,
            rooms_limit=pages.online_rooms_limit,
            matches_limit=pages.online_matches_limit,
            weekly_limit=pages.online_weekly_limit,
            match_players_limit=pages.online_match_players_limit,
            progression=progression,
            room_name_template=pages.room_name_template,
            levels_batch_size=pages.levels_batch_size,
        ),
        get_room=GetRoomPage(
            match,
            progression,
            concurrency=up.max_concurrency,
            room_name_template=pages.room_name_template,
            levels_batch_size=pages.levels_batch_size,
        ),
        get_leaderboards=GetLeaderboardsPage(rating, concurrency=up.max_concurrency),
        get_match=GetMatchPage(
            match,
            catalog,
            library,
            progression,
            concurrency=up.max_concurrency,
            items_deadline_s=pages.match_items_deadline_s,
            items_batch_size=pages.match_items_batch_size,
            appearance_batch_size=pages.appearance_batch_size,
            social=social,
            relations_batch_size=pages.relations_batch_size,
        ),
        http_cache=settings.http_cache,
    )
