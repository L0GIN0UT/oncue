"""Генераторы этапа 3a: реверс и аудио фильмов, последовательность кадров, тексты (M3)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from media_pipeline_service.application.errors import GenerationError
from media_pipeline_service.application.generators import (
    AudioBlackoutGenerator,
    AudioReverseGenerator,
    DescriptionGenerator,
    EmojiGenerator,
    FrameSequenceGenerator,
    SoundtrackGenerator,
)
from media_pipeline_service.domain.clues import (
    AudioReverseParams,
    DescriptionParams,
    EmojiParams,
    FrameSequenceParams,
    SoundtrackParams,
)
from media_pipeline_service.domain.sources import SourceKind, SourceMedia, SourceOrigin
from media_pipeline_service.domain.texts import (
    description_levels,
    emoji_levels,
    graphemes,
    text_content,
)
from pydantic import ValidationError

from gv_core import uuid7

pytestmark = pytest.mark.unit

ITEM = UUID(int=7)
NOW = datetime(2026, 10, 16, tzinfo=UTC)


def frame(position: int, origin: SourceOrigin = SourceOrigin.TMDB) -> SourceMedia:
    return SourceMedia(
        uuid7(),
        media_item_id=ITEM,
        kind=SourceKind.FRAME,
        origin=origin,
        uri=f"https://image.tmdb.org/{position}.jpg",
        created_at=NOW,
        position=position,
    )


def text_source(content: dict[str, Any]) -> SourceMedia:
    return SourceMedia.text(
        uuid7(), media_item_id=ITEM, content=content, sha256="0" * 64, size_bytes=1, created_at=NOW
    )


FILM = text_content(
    clue_texts={"plot_masked": "Учёный … отправляется сквозь червоточину.", "tagline": "Шаг"},
    year=2014,
    genres=["фантастика", "драма"],
    countries=["US", "GB"],
    directors=["Режиссёр Синтетический"],
    cast=["Актёр 1", "Актёр 2", "Актёр 3", "Актёр 4"],
)


# ---------------------------------------------------------------- аудио
async def test_audio_reverse_cuts_from_start_and_reverses(media: Any) -> None:
    params = AudioReverseParams.from_mapping(
        {"start_s": 30, "levels": [{"seconds": 0.5}, {"seconds": 2}]}
    )
    generator = AudioReverseGenerator(media.transcoder)
    source = bytes(range(256)) * 20  # 5120 «байт-сэмплов» по 10 мс = 51.2 с
    assets = await generator.generate([source], params)
    assert media.transcoder.reversed_cuts == [(30, 0.5), (30, 2)]
    # уровень 1: [30 с, 30.5 с) задом наперёд — ни одного байта после 30.5 с
    assert assets[0].data == source[3000:3050][::-1]
    assert [a.duration_ms for a in assets] == [500, 2000]


async def test_audio_reverse_too_short_source_fails_permanently(media: Any) -> None:
    params = AudioReverseParams.from_mapping({"start_s": 30, "levels": [{"seconds": 15}]})
    with pytest.raises(GenerationError) as exc:
        await AudioReverseGenerator(media.transcoder).generate([bytes(1000)], params)
    assert (exc.value.code, exc.value.retryable) == ("source.too_short", False)


def test_film_audio_generators_differ_only_by_source_kind(media: Any) -> None:
    soundtrack = SoundtrackGenerator(media.transcoder)
    blackout = AudioBlackoutGenerator(media.transcoder)
    assert (soundtrack.source_kind, blackout.source_kind) == (
        SourceKind.SOUNDTRACK,
        SourceKind.AUDIO_CLIP,
    )
    clip = SourceMedia(
        uuid7(), media_item_id=ITEM, kind=SourceKind.AUDIO_CLIP, origin=SourceOrigin.UPLOAD,
        uri=None, created_at=NOW,
    )  # fmt: skip
    params = SoundtrackParams.from_mapping({"levels": [{"seconds": 1}]})
    assert soundtrack.sources_for(params, [clip]) is None
    assert blackout.sources_for(params, [clip]) == [clip]


# ---------------------------------------------------------------- кадры
def test_frame_sequence_needs_a_frame_per_level(media: Any) -> None:
    generator = FrameSequenceGenerator(media.images)
    params = FrameSequenceParams.from_mapping(
        {"levels": [{"frame": 2, "max_width": 640}, {"frame": 1, "max_width": 640}]}
    )
    one, two = frame(1), frame(2)
    better_two = frame(2, SourceOrigin.UPLOAD)
    assert generator.source_count(params) == 2
    assert generator.sources_for(params, [one]) is None
    assert generator.sources_for(params, [one, two]) == [two, one]
    assert generator.sources_for(params, [one, two, better_two]) == [better_two, one]


async def test_frame_sequence_renders_each_level_from_its_frame(media: Any) -> None:
    params = FrameSequenceParams.from_mapping(
        {"levels": [{"frame": 1, "max_width": 320}, {"frame": 2, "max_width": 640}]}
    )
    assets = await FrameSequenceGenerator(media.images).generate([b"a", b"b"], params)
    assert [op.max_width for op in media.images.ops] == [320, 640]
    assert all(op.blur_px == 0 and op.crop == 1 for op in media.images.ops)
    assert len(assets) == 2
    with pytest.raises(GenerationError):
        await FrameSequenceGenerator(media.images).generate([b"a"], params)


def test_frame_sequence_rejects_repeated_frame() -> None:
    with pytest.raises(ValidationError):
        FrameSequenceParams.from_mapping(
            {"levels": [{"frame": 1, "max_width": 640}, {"frame": 1, "max_width": 960}]}
        )


# ---------------------------------------------------------------- тексты
def test_text_content_has_no_title_and_top3_cast() -> None:
    assert FILM["cast"] == ["Актёр 1", "Актёр 2", "Актёр 3"]
    assert set(FILM) == {
        "version", "plot_masked", "tagline", "year", "genres", "countries", "directors", "cast",
    }  # fmt: skip
    assert "emoji" not in FILM


def test_description_levels_accumulate_fields() -> None:
    params = DescriptionParams.from_mapping(
        {
            "levels": [
                {"fields": ["tagline"]},
                {"fields": ["year", "genres"]},
                {"fields": ["plot_masked", "country"]},
                {"fields": ["director", "cast_top3"]},
            ]
        }
    )
    levels = description_levels(FILM, params)
    assert levels[0] == {"version": 1, "blocks": [{"kind": "tagline", "text": "Шаг"}]}
    assert levels[1]["blocks"][1] == {
        "kind": "fields",
        "items": [
            {"field": "year", "value": "2014"},
            {"field": "genres", "value": "фантастика, драма"},
        ],
    }
    last = levels[-1]["blocks"]
    assert [b["kind"] for b in last] == ["tagline", "fields", "plot"]
    assert [i["field"] for i in last[1]["items"]] == [
        "year", "genres", "country", "director", "cast_top3",
    ]  # fmt: skip
    assert last[1]["items"][-1]["value"] == "Актёр 1, Актёр 2, Актёр 3"
    # каждый следующий уровень содержит всё, что было открыто раньше
    for prev, cur in pairwise(levels):
        assert json.dumps(prev["blocks"][0]) in json.dumps(cur["blocks"])


def test_description_skips_item_without_needed_field() -> None:
    params = DescriptionParams.from_mapping({"levels": [{"fields": ["tagline"]}]})
    generator = DescriptionGenerator()
    without = text_content(
        clue_texts={"plot_masked": "…"}, year=2000, genres=[], countries=[], directors=[], cast=[]
    )
    assert generator.sources_for(params, [text_source(without)]) is None
    with_tagline = text_source(FILM)
    assert generator.sources_for(params, [with_tagline]) == [with_tagline]


def test_description_field_cannot_reopen() -> None:
    with pytest.raises(ValidationError):
        DescriptionParams.from_mapping({"levels": [{"fields": ["year"]}, {"fields": ["year"]}]})


async def test_description_generator_emits_json_assets() -> None:
    params = DescriptionParams.from_mapping({"levels": [{"fields": ["plot_masked"]}]})
    (asset,) = await DescriptionGenerator().generate([json.dumps(FILM).encode()], params)
    assert (asset.mime, asset.ext) == ("application/json", "json")
    assert json.loads(asset.data)["blocks"] == [
        {"kind": "plot", "text": "Учёный … отправляется сквозь червоточину."}
    ]
    with pytest.raises(GenerationError) as exc:
        await DescriptionGenerator().generate([b"[]"], params)
    assert exc.value.code == "source.undecodable"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("🎬🚀", ["🎬", "🚀"]),
        ("👨‍👩‍👧", ["👨‍👩‍👧"]),  # ZWJ-последовательность
        ("👍🏽❤️", ["👍🏽", "❤️"]),  # тон кожи, вариационный селектор
        ("🇷🇺🇺🇸", ["🇷🇺", "🇺🇸"]),  # флаги — пары индикаторов
        ("1️⃣й", ["1️⃣", "й"]),  # keycap; «й» из двух кодовых точек
        ("", []),
    ],
)
def test_graphemes(text: str, expected: list[str]) -> None:
    assert graphemes(text) == expected


@given(st.text(max_size=40))
def test_graphemes_partition_text(text: str) -> None:
    parts = graphemes(text)
    assert "".join(parts) == text
    assert all(parts)


def test_emoji_levels_open_first_graphemes() -> None:
    params = EmojiParams.from_mapping({"levels": [{"count": 1}, {"count": 3}, {"count": None}]})
    content = {"version": 1, "emoji": "🚀👨‍👩‍👧🌌⏳🇷🇺"}
    levels = [lvl["blocks"][0] for lvl in emoji_levels(content, params)]
    assert [b["text"] for b in levels] == ["🚀", "🚀👨‍👩‍👧🌌", "🚀👨‍👩‍👧🌌⏳🇷🇺"]
    assert {b["kind"] for b in levels} == {"emoji"}


def test_emoji_needs_enough_graphemes() -> None:
    params = EmojiParams.from_mapping({"levels": [{"count": 2}, {"count": 4}]})
    generator = EmojiGenerator()
    assert generator.sources_for(params, [text_source({"version": 1, "emoji": "🚀🌌⏳"})]) is None
    enough = text_source({"version": 1, "emoji": "🚀🌌⏳🇷🇺"})
    assert generator.sources_for(params, [enough]) == [enough]
    assert generator.sources_for(params, [text_source(FILM)]) is None


@pytest.mark.parametrize(
    "levels",
    [
        [{"count": None}, {"count": 2}],  # «все» не последним
        [{"count": 3}, {"count": 2}],
        [{"count": 2}, {"count": 2}],
    ],
)
def test_emoji_levels_must_grow(levels: list[dict[str, Any]]) -> None:
    with pytest.raises(ValidationError):
        EmojiParams.from_mapping({"levels": levels})
