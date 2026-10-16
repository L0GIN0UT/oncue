"""Политика содержимого (K3): golden-отказы с кодами и нормализация перед проверками."""

from __future__ import annotations

import dataclasses
from typing import Any
from uuid import UUID

import pytest
from collections_service.domain.errors import (
    DraftInvalidError,
    PublishInvalidError,
    TitleInvalidError,
)
from collections_service.domain.model import Domain, DraftBody, Item, Visibility
from collections_service.domain.policy import (
    CollectionPolicy,
    CoverPreset,
    ItemInfo,
    Limits,
    MetaConfig,
    Tag,
)

from gv_core.ids import uuid7
from gv_text.ugc import UgcPolicy

pytestmark = pytest.mark.unit

LIMITS = Limits(
    title_max=20, description_max=30, items_min=2, items_max=4, tags_max=2, emoji_max_graphemes=3
)
META = MetaConfig(
    cover_presets=(CoverPreset(id="night", gradient=("#000000", "#111111"), emoji="🌙"),),
    tags=(
        Tag(key="horror", title_key="t.horror", domains=(Domain.FILM,), search_terms=("ужасы",)),
        Tag(key="rock", title_key="t.rock", domains=(Domain.MUSIC,)),
        Tag(key="classic", title_key="t.classic", domains=(Domain.FILM, Domain.MUSIC)),
    ),
)
POLICY = CollectionPolicy(LIMITS, UgcPolicy.from_entries(["дурак*", "idiot"]), META)
A, B, C = uuid7(), uuid7(), uuid7()
FILM = ItemInfo(domain="film", status="active")
KNOWN: dict[UUID, ItemInfo] = {A: FILM, B: FILM, C: FILM}


def draft(**extra: Any) -> DraftBody:
    values: dict[str, Any] = {
        "title": "Кино",
        "default_mode_key": "film.collection.frame",
        "items": (Item(A), Item(B)),
    } | extra
    return DraftBody(**values)


def errors_of(body: DraftBody, known: dict[UUID, ItemInfo] = KNOWN) -> set[tuple[str, str]]:
    with pytest.raises(PublishInvalidError) as exc:
        POLICY.publishable(Domain.FILM, body, known)
    return {(e["path"], e["code"]) for e in exc.value.details["errors"]}


GOLDEN: list[tuple[str, dict[str, Any], set[tuple[str, str]]]] = [
    ("title empty", {"title": " \u200b "}, {("title", "collections.title_invalid")}),
    ("title too long", {"title": "я" * 21}, {("title", "collections.title_invalid")}),
    ("title link", {"title": "см. kino.ru"}, {("title", "collections.title_invalid")}),
    ("title denylist", {"title": "Дураки 90-х"}, {("title", "collections.title_invalid")}),
    ("title confusable", {"title": "id1ot"}, set()),
    (
        "description long",
        {"description": "a" * 31},
        {("description", "collections.description_invalid")},
    ),
    (
        "description denied",
        {"description": "для idiot"},
        {("description", "collections.description_invalid")},
    ),
    ("tag unknown", {"tags": ("nope",)}, {("tags[0]", "collections.tag_unknown")}),
    ("tag other domain", {"tags": ("rock",)}, {("tags[0]", "collections.tag_unknown")}),
    (
        "tags too many",
        {"tags": ("horror", "classic", "nope")},
        {("tags", "collections.tags_too_many"), ("tags[2]", "collections.tag_unknown")},
    ),
    ("cover unknown", {"cover": "sunset"}, {("cover.preset", "collections.cover_unknown")}),
    (
        "mode missing",
        {"default_mode_key": None},
        {("default_mode_key", "collections.mode_invalid")},
    ),
    (
        "mode other domain",
        {"default_mode_key": "music.collection.intro"},
        {("default_mode_key", "collections.mode_invalid")},
    ),
    (
        "mode not collection",
        {"default_mode_key": "film.random.frame"},
        {("default_mode_key", "collections.mode_invalid")},
    ),
    ("items too few", {"items": (Item(A),)}, {("items", "collections.items_too_few")}),
    (
        "items too many",
        {"items": (Item(A), Item(B), Item(C), Item(A), Item(B))},
        {
            ("items", "collections.items_too_many"),
            ("items[3]", "collections.item_duplicate"),
            ("items[4]", "collections.item_duplicate"),
        },
    ),
    ("item duplicate", {"items": (Item(A), Item(A))}, {("items[1]", "collections.item_duplicate")}),
    (
        "item unknown",
        {"items": (Item(A), Item(uuid7()))},
        {("items[1]", "collections.item_unavailable")},
    ),
    (
        "emoji text",
        {"items": (Item(A, "кот"), Item(B))},
        {("items[0].custom.emoji", "collections.emoji_invalid")},
    ),
    (
        "emoji space",
        {"items": (Item(A, "🎬 🍿"), Item(B))},
        {("items[0].custom.emoji", "collections.emoji_invalid")},
    ),
    (
        "emoji too many",
        {"items": (Item(A, "🎬🍿🎥🎞"), Item(B))},
        {("items[0].custom.emoji", "collections.emoji_invalid")},
    ),
    (
        "emoji bare zwj",
        {"items": (Item(A, "\u200d🎬"), Item(B))},
        {("items[0].custom.emoji", "collections.emoji_invalid")},
    ),
    ("emoji zwj family ok", {"items": (Item(A, "👨\u200d👩\u200d👧"), Item(B))}, set()),
    ("emoji flag+skin ok", {"items": (Item(A, "🇷🇺👍🏽"), Item(B))}, set()),
]


@pytest.mark.parametrize(("case", "extra", "expected"), GOLDEN, ids=[g[0] for g in GOLDEN])
def test_publish_golden(case: str, extra: dict[str, Any], expected: set[tuple[str, str]]) -> None:
    body = draft(**extra)
    if not expected:
        assert POLICY.publishable(Domain.FILM, body, KNOWN).items == body.items
        return
    assert errors_of(body) == expected, case


def test_item_status_and_domain() -> None:
    known = {A: ItemInfo("film", "blocked"), B: ItemInfo("music", "active")}
    assert errors_of(draft(), known) == {
        ("items[0]", "collections.item_unavailable"),
        ("items[1]", "collections.item_domain_mismatch"),
    }


def test_emoji_shorter_than_media_profile_is_rejected() -> None:
    """Профиль ``emoji`` media открывает до 6 эмодзи: из меньшего числа набор не строится,
    поэтому короткие свои эмодзи отклоняются при публикации (графемы, не кодовые точки)."""
    limits = dataclasses.replace(LIMITS, emoji_max_graphemes=12, emoji_min_graphemes=6)
    policy = CollectionPolicy(limits, UgcPolicy.from_entries([]), META)
    family = "👨‍👩‍👧"  # одна графема из пяти кодовых точек
    body = draft(items=(Item(A, "🎬🍿🎥🎭🌟"), Item(B, family * 5), Item(C, "кот")))
    with pytest.raises(PublishInvalidError) as exc:
        policy.publishable(Domain.FILM, body, KNOWN)
    assert exc.value.details["errors"] == [
        {"path": "items[0].custom.emoji", "code": "collections.emoji_too_short", "min": 6},
        {"path": "items[1].custom.emoji", "code": "collections.emoji_too_short", "min": 6},
        {"path": "items[2].custom.emoji", "code": "collections.emoji_invalid"},
    ]
    ok = draft(items=(Item(A, "🎬🍿🎥🎭🌟🎻"), Item(B, family * 6)))
    assert policy.publishable(Domain.FILM, ok, KNOWN).items == ok.items


def test_emoji_min_not_above_max() -> None:
    from collections_service.settings import CollectionsLimits
    from pydantic import ValidationError

    assert CollectionsLimits().emoji_min_graphemes == 6
    with pytest.raises(ValidationError):
        CollectionsLimits(emoji_max_graphemes=4, emoji_min_graphemes=6)


def test_title_reason_is_kept_in_errors() -> None:
    with pytest.raises(PublishInvalidError) as exc:
        POLICY.publishable(Domain.FILM, draft(title="пиши на a@b.ru"), KNOWN)
    (error,) = exc.value.details["errors"]
    assert error["reason"] == "ugc.email_forbidden"


def test_publishable_normalizes_text() -> None:
    body = draft(
        title="  Кино\u202e  кадры ",
        description="  строка 1\n\n\n\nстрока 2  ",
        tags=("horror", "horror"),
        cover="night",
        visibility=Visibility.UNLISTED,
    )
    result = POLICY.publishable(Domain.FILM, body, KNOWN)
    assert result.title == "Кино кадры"
    assert result.description == "строка 1\n\nстрока 2"
    assert result.tags == ("horror",) and result.cover == "night"
    assert result.visibility is Visibility.UNLISTED
    assert POLICY.publishable(Domain.FILM, draft(description=" \u200b "), KNOWN).description is None


def test_title_check_returns_reason() -> None:
    assert POLICY.title(" Кино ") == "Кино"
    with pytest.raises(TitleInvalidError) as exc:
        POLICY.title("я" * 21)
    assert exc.value.details == {"reason": "ugc.too_long", "max_len": 20, "length": 21}


def test_draft_limits() -> None:
    POLICY.check_draft(draft(items=()))
    with pytest.raises(DraftInvalidError) as exc:
        POLICY.check_draft(
            draft(items=tuple(Item(uuid7()) for _ in range(5)), tags=("a", "b", "c"))
        )
    assert {e["code"] for e in exc.value.details["errors"]} == {
        "collections.items_too_many",
        "collections.tags_too_many",
    }


def test_meta_lookup_and_search_terms() -> None:
    assert META.has_cover("night") and not META.has_cover("x")
    assert META.tag("rock") is not None and META.tag("x") is None
    assert META.search_terms(["horror", "made_up"]) == ["horror", "ужасы", "made up"]
