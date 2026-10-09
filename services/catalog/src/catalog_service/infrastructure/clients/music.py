"""Источники метаданных треков (``MusicMetadataProvider``, ADR 0014).

Все клиенты — **только метаданные** (название, артисты, год, длительность, популярность,
ISRC). Аудио не запрашивается и не скачивается ни одним из них; эндпоинты загрузки
(например, ``download-info`` у Яндекса) намеренно не реализованы.
"""

from __future__ import annotations

import base64
import re
import time
from collections.abc import Callable
from typing import Any

from catalog_service.application.dto import ImportedItem, PersonRef
from catalog_service.domain.model import (
    ExternalSource,
    MediaKind,
    MediaMetrics,
    PersonRole,
)
from catalog_service.infrastructure.clients.resilience import ResilientHttp
from gv_core.errors import DependencyUnavailableError, NotFoundError

_LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')
_TOKEN_SAFETY_S = 60.0


def _year(value: str | int | None) -> int | None:
    text = str(value or "")[:4]
    return int(text) if text.isdigit() else None


def _artists(names: list[tuple[str | None, str]], source: str) -> tuple[PersonRef, ...]:
    """Артисты: ключ по id источника или по имени, если id нет."""
    refs: list[PersonRef] = []
    for order, (ext_id, name) in enumerate(names):
        if not name:
            continue
        key = f"{source}:{ext_id}" if ext_id else f"name:{name.casefold()}"
        refs.append(PersonRef(key, name, PersonRole.ARTIST, order))
    return tuple(refs)


# ---------------------------------------------------------------------- MusicBrainz


class MusicBrainzProvider:
    """MusicBrainz WS/2 (данные CC0). Требует User-Agent и не больше 1 запроса в секунду."""

    def __init__(self, http: ResilientHttp) -> None:
        self._http = http

    async def get_track(self, ref: str) -> ImportedItem:
        """Запись (recording) по MBID."""
        data = await self._http.get_json(
            f"/recording/{ref}",
            params={"inc": "artist-credits+isrcs+releases+genres", "fmt": "json"},
        )
        return parse_musicbrainz_recording(data)

    async def search_tracks(self, query: str, limit: int) -> list[ImportedItem]:
        """Поиск записей (Lucene-спецсимволы экранируются)."""
        data = await self._http.get_json(
            "/recording",
            params={"query": _LUCENE_SPECIAL.sub(r"\\\1", query), "limit": limit, "fmt": "json"},
        )
        return [parse_musicbrainz_recording(r) for r in data.get("recordings", [])[:limit]]


def parse_musicbrainz_recording(data: dict[str, Any]) -> ImportedItem:
    """Recording → ``ImportedItem``."""
    artist_credits = [
        ((c.get("artist") or {}).get("id"), c.get("name") or (c.get("artist") or {}).get("name"))
        for c in data.get("artist-credit", [])
    ]
    years = [_year(r.get("date")) for r in data.get("releases", [])]
    year = _year(data.get("first-release-date")) or min((y for y in years if y), default=None)
    refs = {ExternalSource.MUSICBRAINZ: str(data["id"])}
    isrcs = data.get("isrcs") or []
    if isrcs:
        refs[ExternalSource.ISRC] = str(isrcs[0])
    return ImportedItem(
        kind=MediaKind.TRACK,
        title=data["title"],
        year=year,
        persons=_artists([(i, n or "") for i, n in artist_credits], "musicbrainz"),
        genres=tuple(g["name"] for g in data.get("genres", []) if g.get("name")),
        external_refs=refs,
        metrics=MediaMetrics(duration_ms=data.get("length")),
    )


# ---------------------------------------------------------------------- Spotify


class SpotifyMetadataProvider:
    """Spotify Web API, client credentials: поиск, треки, популярность. Токен кэшируется."""

    def __init__(
        self,
        http: ResilientHttp,
        auth_http: ResilientHttp,
        *,
        client_id: str,
        client_secret: str,
        token_url: str,
        market: str = "RU",
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._auth = auth_http
        self._basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        self._token_url = token_url
        self._market = market
        self._monotonic = monotonic
        self._token: str | None = None
        self._expires_at = 0.0

    async def _bearer(self) -> dict[str, str]:
        if self._token is None or self._monotonic() >= self._expires_at:
            response = await self._auth.request(
                "POST",
                self._token_url,
                data={"grant_type": "client_credentials"},
                headers={"Authorization": f"Basic {self._basic}"},
            )
            body = response.json()
            token = body.get("access_token")
            if not isinstance(token, str):
                raise DependencyUnavailableError(
                    "Spotify не выдал токен", code="catalog.source_unavailable"
                )
            self._token = token
            self._expires_at = (
                self._monotonic() + float(body.get("expires_in", 3600)) - _TOKEN_SAFETY_S
            )
        return {"Authorization": f"Bearer {self._token}"}

    async def get_track(self, ref: str) -> ImportedItem:
        """Трек по Spotify id."""
        data = await self._http.get_json(
            f"/tracks/{ref}", params={"market": self._market}, headers=await self._bearer()
        )
        return parse_spotify_track(data)

    async def search_tracks(self, query: str, limit: int) -> list[ImportedItem]:
        """Поиск треков."""
        data = await self._http.get_json(
            "/search",
            params={"q": query, "type": "track", "limit": limit, "market": self._market},
            headers=await self._bearer(),
        )
        return [parse_spotify_track(t) for t in (data.get("tracks") or {}).get("items", [])]


def parse_spotify_track(data: dict[str, Any]) -> ImportedItem:
    """Track object → ``ImportedItem``."""
    refs = {ExternalSource.SPOTIFY: str(data["id"])}
    isrc = (data.get("external_ids") or {}).get("isrc")
    if isrc:
        refs[ExternalSource.ISRC] = str(isrc)
    return ImportedItem(
        kind=MediaKind.TRACK,
        title=data["name"],
        year=_year((data.get("album") or {}).get("release_date")),
        persons=_artists(
            [(a.get("id"), a.get("name", "")) for a in data.get("artists", [])], "spotify"
        ),
        external_refs=refs,
        metrics=MediaMetrics(
            popularity=data.get("popularity"), duration_ms=data.get("duration_ms")
        ),
    )


# ---------------------------------------------------------------------- Яндекс Музыка


class YandexMusicMetadataProvider:
    """Неофициальный клиент Яндекс Музыки — только поиск и карточка трека.

    Официального API нет; риск нарушения условий сервиса принят владельцем (ADR 0014).
    Выключен по умолчанию, изолирован за портом и собственным circuit breaker: если
    эндпоинты изменятся или доступ закроют, каталог продолжит работать на других источниках.
    """

    def __init__(self, http: ResilientHttp) -> None:
        self._http = http

    async def get_track(self, ref: str) -> ImportedItem:
        """Трек по id Яндекс Музыки."""
        data = await self._http.get_json(f"/tracks/{ref}")
        tracks = data.get("result") or []
        if not tracks:
            raise NotFoundError("трек не найден", code="catalog.external_not_found")
        return parse_yandex_track(tracks[0])

    async def search_tracks(self, query: str, limit: int) -> list[ImportedItem]:
        """Поиск треков (первая страница)."""
        data = await self._http.get_json(
            "/search", params={"text": query, "type": "track", "page": 0}
        )
        results = (((data.get("result") or {}).get("tracks") or {}).get("results")) or []
        return [parse_yandex_track(t) for t in results[:limit]]


def parse_yandex_track(data: dict[str, Any]) -> ImportedItem:
    """Трек из ответа Яндекс Музыки → ``ImportedItem``."""
    albums = data.get("albums") or []
    years = [_year(a.get("year")) for a in albums]
    genres = tuple(a["genre"] for a in albums[:1] if a.get("genre"))
    return ImportedItem(
        kind=MediaKind.TRACK,
        title=data["title"],
        year=min((y for y in years if y), default=None),
        persons=_artists(
            [
                (str(a["id"]) if a.get("id") else None, a.get("name", ""))
                for a in data.get("artists", [])
            ],
            "yandex_music",
        ),
        genres=genres,
        external_refs={ExternalSource.YANDEX_MUSIC: str(data["id"])},
        metrics=MediaMetrics(duration_ms=data.get("durationMs")),
    )
