"""TMDB: метаданные фильмов (``FilmMetadataProvider``).

Изображения запрашиваются с ``include_image_language=null`` и берутся только из
``images.posters``/``images.backdrops`` — то есть **без надписей**: постер с названием
выдал бы ответ загадки. ``poster_path`` из карточки не используется (он локализован).
"""

from __future__ import annotations

from typing import Any

from catalog_service.application.dto import ImportedItem, PersonRef
from catalog_service.domain.model import (
    AliasDraft,
    AliasKind,
    ExternalSource,
    MediaKind,
    MediaMetrics,
    PersonRole,
    SourceMediaDraft,
    SourceMediaKind,
    SourceMediaOrigin,
)
from catalog_service.infrastructure.clients.resilience import ResilientHttp

MAX_ALT_TITLES = 20
MAX_ACTORS = 5
MAX_FRAMES = 3


class TmdbClient:
    """Клиент TMDB API v3 (Bearer-токен v4 передаётся в заголовках ``http``)."""

    def __init__(
        self,
        http: ResilientHttp,
        *,
        language: str = "ru-RU",
        fallback_language: str = "en-US",
        image_base_url: str = "https://image.tmdb.org/t/p/original",
    ) -> None:
        self._http = http
        self._language = language
        self._fallback = fallback_language
        self._images = image_base_url.rstrip("/")

    async def fetch_film(self, ref: str) -> ImportedItem:
        """Фильм по TMDB id."""
        data: dict[str, Any] = await self._http.get_json(
            f"/movie/{ref}",
            params={
                "language": self._language,
                "append_to_response": "alternative_titles,translations,credits,images",
                "include_image_language": "null",
            },
        )
        return parse_movie(data, self._language, self._fallback, self._images)


def _lang(code: str) -> str:
    return code.split("-", maxsplit=1)[0].lower()


def parse_movie(
    data: dict[str, Any], language: str, fallback_language: str, image_base: str
) -> ImportedItem:
    """Ответ ``/movie/{id}`` → ``ImportedItem``."""
    lang, fallback = _lang(language), _lang(fallback_language)
    translations = {
        _lang(t.get("iso_639_1") or ""): (t.get("data") or {}).get("title") or ""
        for t in (data.get("translations") or {}).get("translations", [])
    }
    original = data.get("original_title") or None
    original_lang = data.get("original_language") or None
    title = (
        translations.get(lang)
        or (data.get("title") if original_lang == lang or data.get("title") != original else "")
        or translations.get(fallback)
        or data.get("title")
        or original
        or ""
    )

    aliases: list[AliasDraft] = [AliasDraft(title, AliasKind.TITLE, lang)]
    if translations.get(fallback):
        aliases.append(AliasDraft(translations[fallback], AliasKind.ALT, fallback))
    if original:
        aliases.append(AliasDraft(original, AliasKind.ALT, original_lang))
    alt_titles = (data.get("alternative_titles") or {}).get("titles", [])[:MAX_ALT_TITLES]
    aliases.extend(
        AliasDraft(a["title"], AliasKind.ALT, None) for a in alt_titles if a.get("title")
    )

    crew_and_cast = data.get("credits") or {}
    directors = [c for c in crew_and_cast.get("crew", []) if c.get("job") == "Director"]
    cast = sorted(crew_and_cast.get("cast", []), key=lambda c: c.get("order", 0))[:MAX_ACTORS]
    persons = [
        PersonRef(f"tmdb:{p['id']}", p["name"], role, order)
        for role, people in ((PersonRole.DIRECTOR, directors), (PersonRole.ACTOR, cast))
        for order, p in enumerate(people)
    ]

    images = data.get("images") or {}
    picked = [(SourceMediaKind.POSTER, i) for i in _no_text(images.get("posters", []))[:1]] + [
        (SourceMediaKind.FRAME, i) for i in _no_text(images.get("backdrops", []))[:MAX_FRAMES]
    ]
    media = [
        SourceMediaDraft(kind, SourceMediaOrigin.TMDB, image_base + img["file_path"])
        for kind, img in picked
    ]

    release = data.get("release_date") or ""
    return ImportedItem(
        kind=MediaKind.FILM,
        title=title,
        original_title=original,
        year=int(release[:4]) if release[:4].isdigit() else None,
        aliases=tuple(aliases),
        persons=tuple(persons),
        genres=tuple(g["name"] for g in data.get("genres", []) if g.get("name")),
        external_refs={ExternalSource.TMDB: str(data["id"])},
        metrics=MediaMetrics(
            popularity=data.get("popularity"),
            vote_average=data.get("vote_average"),
            vote_count=data.get("vote_count"),
            revenue_usd=data.get("revenue") or None,
            runtime_min=data.get("runtime") or None,
        ),
        source_media=tuple(media),
    )


def _no_text(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Только изображения без языка (без надписей), лучшие по голосам первыми."""
    clean = [i for i in images if i.get("iso_639_1") is None and i.get("file_path")]
    return sorted(clean, key=lambda i: (-(i.get("vote_average") or 0), i["file_path"]))
