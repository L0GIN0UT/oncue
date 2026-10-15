"""TMDB: метаданные фильмов (``FilmMetadataProvider``).

Изображения запрашиваются с ``include_image_language=null`` и берутся только из
``images.posters``/``images.backdrops`` — то есть **без надписей**: постер с названием
выдал бы ответ загадки. ``poster_path`` из карточки не используется (он локализован).

Этап 3a: ``overview`` (на языке запроса) — исходный сюжет загадки ``description`` (до
одобрения модератором в события не попадает, ADR 0024), ``tagline``,
``production_countries`` и до ``GV_CATALOG__TMDB_FRAMES_MAX`` кадров с ``position``
(1 — лучший по голосам). У каждого исходника — ``source_url`` (страница фильма на TMDB)
и ``license_note`` (D6).
"""

from __future__ import annotations

from typing import Any

from catalog_service.application.dto import ImportedClueTexts, ImportedItem, PersonRef
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
DEFAULT_FRAMES = 6
DEFAULT_LICENSE_NOTE = "TMDB API (атрибуция TMDB)"
TMDB_MOVIE_URL = "https://www.themoviedb.org/movie/{id}"


class TmdbClient:
    """Клиент TMDB API v3 (Bearer-токен v4 передаётся в заголовках ``http``)."""

    def __init__(
        self,
        http: ResilientHttp,
        *,
        language: str = "ru-RU",
        fallback_language: str = "en-US",
        image_base_url: str = "https://image.tmdb.org/t/p/original",
        frames_max: int = DEFAULT_FRAMES,
        license_note: str = DEFAULT_LICENSE_NOTE,
    ) -> None:
        self._http = http
        self._language = language
        self._fallback = fallback_language
        self._images = image_base_url.rstrip("/")
        self._frames_max = frames_max
        self._license_note = license_note

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
        return parse_movie(
            data,
            self._language,
            self._fallback,
            self._images,
            frames_max=self._frames_max,
            license_note=self._license_note,
        )


def _lang(code: str) -> str:
    return code.split("-", maxsplit=1)[0].lower()


def parse_movie(
    data: dict[str, Any],
    language: str,
    fallback_language: str,
    image_base: str,
    *,
    frames_max: int = DEFAULT_FRAMES,
    license_note: str = DEFAULT_LICENSE_NOTE,
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
    picked = [(SourceMediaKind.POSTER, 1, i) for i in _no_text(images.get("posters", []))[:1]] + [
        (SourceMediaKind.FRAME, position, img)
        for position, img in enumerate(_no_text(images.get("backdrops", []))[:frames_max], 1)
    ]
    page = TMDB_MOVIE_URL.format(id=data["id"])
    media = [
        SourceMediaDraft(
            kind,
            SourceMediaOrigin.TMDB,
            image_base + img["file_path"],
            position=position,
            source_url=page,
            license_note=license_note,
        )
        for kind, position, img in picked
    ]
    countries = tuple(
        c["iso_3166_1"]
        for c in data.get("production_countries") or []
        if isinstance(c.get("iso_3166_1"), str) and len(c["iso_3166_1"]) == 2  # noqa: PLR2004
    )
    overview = (data.get("overview") or "").strip() or None
    tagline = (data.get("tagline") or "").strip() or None

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
        countries=countries,
        clue_texts=(
            ImportedClueTexts(plot_raw=overview, tagline=tagline) if overview or tagline else None
        ),
    )


def _no_text(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Только изображения без языка (без надписей), лучшие по голосам первыми."""
    clean = [i for i in images if i.get("iso_639_1") is None and i.get("file_path")]
    return sorted(clean, key=lambda i: (-(i.get("vote_average") or 0), i["file_path"]))
