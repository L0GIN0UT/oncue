"""Клиенты внешних источников на записанных ответах (respx). Сеть не используется."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from catalog_service.domain.model import (
    AliasKind,
    ExternalSource,
    PersonRole,
    SourceMediaKind,
)
from catalog_service.infrastructure.clients.music import (
    MusicBrainzProvider,
    SpotifyMetadataProvider,
    YandexMusicMetadataProvider,
)
from catalog_service.infrastructure.clients.resilience import CircuitBreaker, ResilientHttp
from catalog_service.infrastructure.clients.tmdb import TmdbClient

from gv_core.errors import DependencyUnavailableError, NotFoundError

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent.parent / "fixtures" / "external"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text("utf-8"))


async def no_sleep(_: float) -> None:
    return None


def http(
    base: str, name: str = "src", *, threshold: int = 5, retries: int = 2, **headers: str
) -> ResilientHttp:
    return ResilientHttp(
        name,
        httpx.AsyncClient(base_url=base, headers=headers),
        rps=1000,
        breaker=CircuitBreaker(name, threshold, 30),
        max_retries=retries,
        backoff_s=0,
        sleep=no_sleep,
    )


TMDB = "https://tmdb.test/3"


@respx.mock
async def test_tmdb_film_without_text_images() -> None:
    route = respx.get(f"{TMDB}/movie/157336").respond(200, json=fixture("tmdb_movie_157336.json"))
    client = TmdbClient(
        http(TMDB, "tmdb", Authorization="Bearer t0ken"), image_base_url="https://img.test/o"
    )
    item = await client.fetch_film("157336")

    request = route.calls.last.request
    assert request.url.params["include_image_language"] == "null"
    assert request.url.params["language"] == "ru-RU"
    assert "credits" in request.url.params["append_to_response"]
    assert request.headers["authorization"] == "Bearer t0ken"

    assert item.title == "Интерстеллар"
    assert item.year == 2014
    assert item.external_refs == {ExternalSource.TMDB: "157336"}
    assert item.metrics.runtime_min == 169
    # Только изображения без языка: постер с надписью и локализованный poster_path не берём.
    uris = {(m.kind, m.uri) for m in item.source_media}
    assert uris == {
        (SourceMediaKind.POSTER, "https://img.test/o/clean_poster.jpg"),
        (SourceMediaKind.FRAME, "https://img.test/o/frame1.jpg"),
    }
    roles = {(p.key, p.role) for p in item.persons}
    assert ("tmdb:525", PersonRole.DIRECTOR) in roles
    assert ("tmdb:10297", PersonRole.ACTOR) in roles
    assert ("tmdb:1", PersonRole.DIRECTOR) not in roles
    texts = {(a.text, a.kind) for a in item.aliases}
    assert ("Interstellar", AliasKind.ALT) in texts
    assert ("Interstellar: IMAX", AliasKind.ALT) in texts


@respx.mock
async def test_tmdb_retries_429_then_succeeds() -> None:
    route = respx.get(f"{TMDB}/movie/157336").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(503),
            httpx.Response(200, json=fixture("tmdb_movie_157336.json")),
        ]
    )
    item = await TmdbClient(http(TMDB)).fetch_film("157336")
    assert item.title == "Интерстеллар"
    assert route.call_count == 3


@respx.mock
async def test_tmdb_not_found() -> None:
    respx.get(f"{TMDB}/movie/1").respond(404, json={"status_code": 34})
    with pytest.raises(NotFoundError) as exc:
        await TmdbClient(http(TMDB)).fetch_film("1")
    assert exc.value.code == "catalog.external_not_found"


@respx.mock
async def test_circuit_opens_after_repeated_failures() -> None:
    route = respx.get(f"{TMDB}/movie/1").respond(500)
    client = TmdbClient(http(TMDB, threshold=2, retries=0))
    for _ in range(2):
        with pytest.raises(DependencyUnavailableError):
            await client.fetch_film("1")
    calls = route.call_count
    with pytest.raises(DependencyUnavailableError):
        await client.fetch_film("1")
    assert route.call_count == calls  # цепь разомкнута — источник не дёргаем


@respx.mock
async def test_auth_error_is_not_retried() -> None:
    route = respx.get(f"{TMDB}/movie/1").respond(401)
    with pytest.raises(DependencyUnavailableError):
        await TmdbClient(http(TMDB)).fetch_film("1")
    assert route.call_count == 1


@respx.mock
async def test_transport_error_retried() -> None:
    route = respx.get(f"{TMDB}/movie/157336").mock(
        side_effect=[
            httpx.ConnectError("boom"),
            httpx.Response(200, json=fixture("tmdb_movie_157336.json")),
        ]
    )
    await TmdbClient(http(TMDB)).fetch_film("157336")
    assert route.call_count == 2


MB = "https://mb.test/ws/2"


@respx.mock
async def test_musicbrainz_get_and_search() -> None:
    mbid = "b1a9c0e9-d987-4042-ae91-78d6a3267d69"
    respx.get(f"{MB}/recording/{mbid}").respond(200, json=fixture("musicbrainz_recording.json"))
    search = respx.get(f"{MB}/recording").respond(200, json=fixture("musicbrainz_search.json"))
    provider = MusicBrainzProvider(http(MB, **{"User-Agent": "Guessverse-test/0"}))

    item = await provider.get_track(mbid)
    assert item.title == "Bohemian Rhapsody"
    assert item.year == 1975
    assert item.external_refs[ExternalSource.ISRC] == "GBUM71029604"
    assert item.persons[0].key == "musicbrainz:0383dadf-2a4e-4d10-a46a-e9e041da8eb3"
    assert item.metrics.duration_ms == 354000

    [hit] = await provider.search_tracks('queen "bohemian"', 5)
    assert hit.title == "Bohemian Rhapsody"
    assert search.calls.last.request.url.params["query"] == 'queen \\"bohemian\\"'


SP = "https://sp.test/v1"
SP_TOKEN = "https://accounts.sp.test/api/token"


@respx.mock
async def test_spotify_token_cached_and_tracks() -> None:
    token = respx.post(SP_TOKEN).respond(200, json={"access_token": "abc", "expires_in": 3600})
    track = respx.get(f"{SP}/tracks/4u7EnebtmKWzUH433cf5Qv").respond(
        200, json=fixture("spotify_track.json")
    )
    respx.get(f"{SP}/search").respond(200, json=fixture("spotify_search.json"))
    provider = SpotifyMetadataProvider(
        http(SP),
        http(SP_TOKEN),
        client_id="id",
        client_secret="secret",
        token_url=SP_TOKEN,
    )
    item = await provider.get_track("4u7EnebtmKWzUH433cf5Qv")
    hits = await provider.search_tracks("bohemian", 5)

    assert token.call_count == 1
    assert token.calls.last.request.headers["authorization"].startswith("Basic ")
    assert track.calls.last.request.headers["authorization"] == "Bearer abc"
    assert item.metrics.popularity == 83
    assert item.year == 1975
    assert item.external_refs[ExternalSource.SPOTIFY] == "4u7EnebtmKWzUH433cf5Qv"
    assert len(hits) == 1


@respx.mock
async def test_spotify_bad_token_response() -> None:
    respx.post(SP_TOKEN).respond(200, json={"error": "x"})
    provider = SpotifyMetadataProvider(
        http(SP), http(SP_TOKEN), client_id="i", client_secret="s", token_url=SP_TOKEN
    )
    with pytest.raises(DependencyUnavailableError):
        await provider.get_track("x")


YM = "https://ym.test"


@respx.mock
async def test_yandex_metadata_only() -> None:
    respx.get(f"{YM}/tracks/3758938").respond(200, json=fixture("yandex_track.json"))
    respx.get(f"{YM}/search").respond(200, json=fixture("yandex_search.json"))
    provider = YandexMusicMetadataProvider(http(YM, Authorization="OAuth t"))
    item = await provider.get_track("3758938")
    hits = await provider.search_tracks("группа крови", 3)
    assert item.title == "Группа крови"
    assert item.year == 1988
    assert item.persons[0].key == "yandex_music:7417"
    assert item.external_refs == {ExternalSource.YANDEX_MUSIC: "3758938"}
    assert [h.title for h in hits] == ["Группа крови"]
    # Только метаданные: к эндпоинтам загрузки аудио клиент не обращается.
    assert all("download" not in str(c.request.url) for c in respx.calls)


@respx.mock
async def test_yandex_empty_result_is_not_found() -> None:
    respx.get(f"{YM}/tracks/1").respond(200, json={"result": []})
    with pytest.raises(NotFoundError):
        await YandexMusicMetadataProvider(http(YM)).get_track("1")


@respx.mock
async def test_tmdb_stage3a_texts_countries_and_frames() -> None:
    """Этап 3a (M8): сюжет и слоган (ru), страны, до frames_max кадров с position,
    source_url и license_note у каждого исходника; кадр с надписью не берётся."""
    respx.get(f"{TMDB}/movie/27205").respond(200, json=fixture("tmdb_movie_27205.json"))
    client = TmdbClient(
        http(TMDB, "tmdb"),
        image_base_url="https://img.test/o",
        frames_max=6,
        license_note="TMDB, атрибуция",
    )
    item = await client.fetch_film("27205")

    assert item.clue_texts is not None
    assert item.clue_texts.plot_raw is not None
    assert item.clue_texts.plot_raw.startswith("Кобб — талантливый вор")
    assert item.clue_texts.tagline == "Твой разум — место преступления"
    assert item.clue_texts.plot_masked is None  # маскирует use-case импорта
    assert item.countries == ("GB", "US")
    frames = [m for m in item.source_media if m.kind is SourceMediaKind.FRAME]
    assert [(m.position, m.uri) for m in frames] == [
        (i, f"https://img.test/o/frame{i}.jpg") for i in range(1, 7)
    ]
    assert {(m.source_url, m.license_note) for m in item.source_media} == {
        ("https://www.themoviedb.org/movie/27205", "TMDB, атрибуция")
    }
    roles = [(p.name, p.role, p.order) for p in item.persons if p.role is PersonRole.ACTOR]
    assert roles[0] == ("Леонардо ДиКаприо", PersonRole.ACTOR, 0)


@respx.mock
async def test_tmdb_without_overview_has_no_clue_texts() -> None:
    respx.get(f"{TMDB}/movie/157336").respond(200, json=fixture("tmdb_movie_157336.json"))
    item = await TmdbClient(http(TMDB, "tmdb"), image_base_url="https://img.test/o").fetch_film(
        "157336"
    )
    assert item.clue_texts is None
    assert item.countries == ()
