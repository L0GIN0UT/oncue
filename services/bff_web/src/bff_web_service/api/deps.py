"""Зависимости роутеров bff_web (собирает composition root ``wiring``)."""

from __future__ import annotations

from dataclasses import dataclass

from bff_web_service.application.queries import (
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
)
from bff_web_service.settings import HttpCacheSettings


@dataclass(frozen=True)
class BffDeps:
    """Запросы экранов и политика ``Cache-Control`` публичных ответов."""

    get_session: GetSession
    get_home: GetHomePage
    get_solo: GetSoloPage
    get_results: GetResultsPage
    get_collections: GetCollectionsPage
    get_collection: GetCollectionPage
    get_library: GetLibraryPage
    get_library_shelf: GetLibraryShelfPage
    get_online: GetOnlinePage
    get_match: GetMatchPage
    get_room: GetRoomPage
    get_leaderboards: GetLeaderboardsPage
    get_profile: GetProfilePage
    get_player: GetPlayerPage
    get_shop: GetShopPage
    get_shell: GetShellPage
    get_event_page: GetEventPage
    http_cache: HttpCacheSettings
