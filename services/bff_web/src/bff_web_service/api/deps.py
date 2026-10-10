"""Зависимости роутеров bff_web (собирает composition root ``wiring``)."""

from __future__ import annotations

from dataclasses import dataclass

from bff_web_service.application.queries import (
    GetHomePage,
    GetResultsPage,
    GetSession,
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
    http_cache: HttpCacheSettings
