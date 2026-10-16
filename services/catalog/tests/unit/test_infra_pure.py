"""Чистая логика адаптеров: тела запросов OpenSearch, разбор файлов импорта, сиды."""

from __future__ import annotations

from pathlib import Path

import pytest
from catalog_service.application.dto import SearchDocument, SearchQuery
from catalog_service.domain.media_item import MediaItem
from catalog_service.domain.model import ExternalSource, MediaDomain, MediaKind
from catalog_service.domain.normalization import TitleNormalizer
from catalog_service.infrastructure.clients.resilience import CircuitBreaker, TokenBucket
from catalog_service.infrastructure.files import FileImportProvider, parse_line
from catalog_service.infrastructure.search.opensearch import (
    autocomplete_body,
    concrete_index,
    document_body,
    index_body,
    parse_hits,
    search_body,
)

from gv_core.errors import DependencyUnavailableError, InvalidInputError, NotFoundError
from gv_core.ids import uuid7

pytestmark = pytest.mark.unit

SEEDS = Path(__file__).resolve().parents[4] / "seeds" / "catalog"


# ------------------------------------------------------------------ OpenSearch


def test_index_mapping_has_edge_ngram_and_languages() -> None:
    body = index_body()
    analysis = body["settings"]["analysis"]
    assert analysis["filter"]["gv_edge"]["type"] == "edge_ngram"
    for field in ("title", "aliases"):
        mapping = body["mappings"]["properties"][field]
        assert mapping["analyzer"] == "gv_autocomplete_index"
        assert mapping["fields"]["ru"]["analyzer"] == "russian"
        assert mapping["fields"]["en"]["analyzer"] == "english"
    assert body["mappings"]["dynamic"] == "strict"
    assert concrete_index("catalog_media") == "catalog_media_v1"


def test_queries_always_filter_active_and_domain() -> None:
    q = SearchQuery("инте", MediaDomain.FILM, 5)
    for body in (autocomplete_body(q), search_body(q)):
        filters = body["query"]["function_score"]["query"]["bool"]["filter"]
        assert {"term": {"status": "active"}} in filters
        assert {"term": {"domain": "film"}} in filters
        assert body["size"] == 5
    no_domain = autocomplete_body(SearchQuery("инте", None, 3))
    assert no_domain["query"]["function_score"]["query"]["bool"]["filter"] == [
        {"term": {"status": "active"}}
    ]


def test_document_body_matches_mapping() -> None:
    from catalog_service.domain.model import ItemData

    item = MediaItem.create(
        uuid7(),
        ItemData(
            kind=MediaKind.FILM,
            title="Интерстеллар",
            year=2014,
            external_refs={ExternalSource.TMDB: "1"},
        ),
        TitleNormalizer(),
        current_year=2026,
    )
    doc = document_body(SearchDocument.from_item(item))
    assert set(doc) <= set(index_body()["mappings"]["properties"])
    assert doc["aliases"] == ["интерстеллар", "interstellar"]
    assert doc["status"] == "active"


def test_parse_hits() -> None:
    item_id = uuid7()
    response = {
        "hits": {
            "hits": [
                {
                    "_source": {
                        "id": str(item_id),
                        "domain": "film",
                        "title": "Т",
                        "year": None,
                        "subtitle": "S",
                    }
                }
            ]
        }
    }
    [hit] = parse_hits(response)
    assert hit.id == item_id
    assert hit.subtitle == "S"
    assert parse_hits({}) == []


# ------------------------------------------------------------------ устойчивость


async def test_token_bucket_waits_when_empty() -> None:
    now = [0.0]
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    bucket = TokenBucket(2, monotonic=lambda: now[0], sleep=sleep)
    for _ in range(3):
        await bucket.acquire()
    assert slept == [pytest.approx(0.5)]


def test_circuit_breaker_half_opens_after_timeout() -> None:
    now = [0.0]
    breaker = CircuitBreaker("x", threshold=2, open_seconds=10, monotonic=lambda: now[0])
    breaker.failure()
    breaker.check()
    breaker.failure()
    with pytest.raises(DependencyUnavailableError):
        breaker.check()
    now[0] = 11
    breaker.check()  # пробная попытка разрешена
    breaker.success()
    assert not breaker.is_open


# ------------------------------------------------------------------ файлы импорта


def test_parse_line_valid() -> None:
    item = parse_line(
        '{"key": "film:x", "kind": "film", "title": "Брат", "year": 1997,'
        ' "persons": [{"name": "Алексей Балабанов", "role": "director"}],'
        ' "external_refs": {"tmdb": "20992"}}',
        1,
    )
    assert not isinstance(item, ValueError)
    assert item.external_refs == {ExternalSource.TMDB: "20992", ExternalSource.SEED: "film:x"}
    assert item.persons[0].key == "name:алексей балабанов"


@pytest.mark.parametrize(
    "line",
    [
        "not json",
        '{"kind": "film", "title": "x"}',
        '{"key": "a", "kind": "series", "title": "x"}',
        '{"key": "a", "kind": "film", "title": "x", "unknown": 1}',
        '{"key": "a", "kind": "film", "title": "x", "metrics": {"vote_average": 11}}',
        '{"key": "a", "kind": "film", "title": "x", "external_refs": {"seed": "z"}}',
    ],
)
def test_parse_line_errors_do_not_leak_content(line: str) -> None:
    result = parse_line(line, 7)
    assert isinstance(result, ValueError)
    assert str(result).startswith("строка 7")


def test_parse_line_too_long() -> None:
    assert isinstance(parse_line("x" * 70_000, 1), ValueError)


async def test_file_provider_rejects_traversal_and_missing(tmp_path: Path) -> None:
    provider = FileImportProvider(tmp_path)
    with pytest.raises(InvalidInputError):
        [x async for x in provider.read("../secret.jsonl")]
    with pytest.raises(NotFoundError):
        [x async for x in provider.read("missing.jsonl")]


async def test_file_provider_skips_comments_and_limits_lines(tmp_path: Path) -> None:
    line = '{"key": "k%d", "kind": "track", "title": "T%d"}'
    content = "# комментарий\n\n" + "\n".join(line % (i, i) for i in range(5))
    (tmp_path / "t.jsonl").write_text(content, "utf-8")
    entries = [e async for e in FileImportProvider(tmp_path, max_lines=4).read("t.jsonl")]
    assert sum(not isinstance(e, ValueError) for e in entries) == 2
    assert isinstance(entries[-1], ValueError)


@pytest.mark.parametrize(
    ("file_id", "kind"),
    [
        ("films.jsonl", "film"),
        ("tracks.jsonl", "track"),
        ("synthetic-films.jsonl", "film"),
        ("synthetic-tracks.jsonl", "track"),
    ],
)
async def test_seeds_are_valid(file_id: str, kind: str) -> None:
    entries = [e async for e in FileImportProvider(SEEDS).read(file_id)]
    errors = [e for e in entries if isinstance(e, ValueError)]
    assert errors == []
    assert len(entries) >= 20
    assert {e.kind.value for e in entries if not isinstance(e, ValueError)} == {kind}
    keys = [e.external_refs[ExternalSource.SEED] for e in entries if not isinstance(e, ValueError)]
    assert len(set(keys)) == len(keys)


# ---------------------------------------------------------------- файловый импорт этапа 3a (M8)
_STAGE3A_LINE = (
    '{"key": "film:synthetic-1", "kind": "film", "title": "Синтетика 1", "year": 2001,'
    ' "countries": ["us", "GB"], "metrics": {"vote_average": 7.5, "popularity": 10},'
    ' "clue_texts": {"plot_masked": "Сюжет …", "tagline": "Слоган", "emoji": "🚀🌌⏳"},'
    ' "source_media": [{"kind": "frame", "origin": "upload", "uri": "https://x.test/f2.jpg",'
    ' "position": 2, "source_url": "https://x.test/", "license_note": "синтетика"}]}'
)


def test_parse_line_stage3a_fields() -> None:
    from catalog_service.domain.model import SourceMediaKind

    item = parse_line(_STAGE3A_LINE, 1)
    assert not isinstance(item, ValueError)
    assert item.countries == ("us", "GB")  # нормализует домен единицы
    assert item.metrics.vote_average == 7.5
    assert item.clue_texts is not None
    assert (item.clue_texts.plot_masked, item.clue_texts.emoji) == ("Сюжет …", "🚀🌌⏳")
    assert not item.texts_authoritative
    (frame,) = item.source_media
    assert (frame.kind, frame.position, frame.license_note) == (
        SourceMediaKind.FRAME,
        2,
        "синтетика",
    )
    approved = parse_line(_STAGE3A_LINE, 1, texts_approved=True)
    assert not isinstance(approved, ValueError) and approved.texts_authoritative


@pytest.mark.parametrize(
    "fragment",
    [
        '"source_media": [{"kind": "frame", "origin": "upload", "uri": "http://x.test/a.jpg"}]',
        '"source_media": [{"kind": "text", "origin": "upload", "uri": "https://x.test/a"}]',
        '"source_media": [{"kind": "frame", "origin": "seed", "uri": "https://x.test/a"}]',
        '"source_media": [{"kind": "frame", "origin": "upload", "uri": "https://x/a",'
        ' "position": 0}]',
        '"clue_texts": {"plot": "нет такого поля"}',
        '"countries": ["US", "GB", "FR", "DE", "IT", "ES", "PT", "NL", "BE", "LU", "AT", "CH",'
        ' "PL", "CZ", "SK", "HU", "RO", "BG", "GR", "TR", "RU"]',
    ],
)
def test_parse_line_stage3a_rejects_bad_fields(fragment: str) -> None:
    line = f'{{"key": "film:x", "kind": "film", "title": "X", {fragment}}}'
    assert isinstance(parse_line(line, 3), ValueError)


def test_parse_line_requires_provenance_outside_local() -> None:
    line = (
        '{"key": "film:x", "kind": "film", "title": "X", "source_media":'
        ' [{"kind": "frame", "origin": "upload", "uri": "https://x.test/a.jpg"}]}'
    )
    assert not isinstance(parse_line(line, 1), ValueError)
    error = parse_line(line, 1, require_provenance=True)
    assert isinstance(error, ValueError)
    assert "source_url" in str(error)
    assert not isinstance(parse_line(_STAGE3A_LINE, 1, require_provenance=True), ValueError)
