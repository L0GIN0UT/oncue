"""Тексты загадок (ADR 0024, план этапа 3a M7): маскировка (golden), ревизии, одобрение,
публикация в снимке единицы."""

from __future__ import annotations

import uuid

import pytest
from catalog_service.domain.clue_texts import (
    ApprovedClueTexts,
    ClueTexts,
    ClueTextStatus,
    graphemes,
    mask_plot,
    validate_emoji,
)
from catalog_service.domain.media_item import MediaItem
from catalog_service.domain.model import ItemData, MediaKind, normalize_countries
from catalog_service.domain.normalization import TitleNormalizer
from hypothesis import given
from hypothesis import strategies as st

from gv_core.errors import ConflictError, DomainError

pytestmark = pytest.mark.unit

ITEM = uuid.UUID(int=5)

# (сюжет, названия/алиасы, персоны) → ожидаемая маска. Регресс: правило меняется только
# вместе с этой таблицей.
MASK_GOLDEN: list[tuple[str, list[str], list[str], str]] = [
    (
        "В фильме «Интерстеллар» Кристофер Нолан отправляет героев сквозь червоточину.",
        ["Интерстеллар", "Interstellar"],
        ["Кристофер Нолан"],
        "В фильме «…» … отправляет героев сквозь червоточину.",
    ),
    (
        "После «Интерстеллара» Нолана ждали новые эксперименты.",
        ["Интерстеллар"],
        ["Кристофер Нолан"],
        "После «…» … ждали новые эксперименты.",
    ),
    (
        "INTERSTELLAR — история Купера.",
        ["Interstellar"],
        [],
        "… — история Купера.",
    ),
    (
        "Матрица, матрица и снова Матрица: Нео выбирает таблетку.",
        ["Матрица", "The Matrix"],
        ["Лана Вачовски", "Лилли Вачовски"],
        "… и снова …: Нео выбирает таблетку.",
    ),
    (
        "Он и она живут в доме Оно.",
        ["Оно"],
        [],
        "Он и она живут в доме ….",
    ),
    (
        "Бойцовский   клуб — фильм Дэвида Финчера о Тайлере.",
        ["Бойцовский клуб"],
        ["Дэвид Финчер"],
        "… — фильм … о Тайлере.",
    ),
    (
        "Сюжет без имён и названий остаётся как есть.",
        ["Начало"],
        ["Кристофер Нолан"],
        "Сюжет без имён и названий остаётся как есть.",
    ),
    (
        "Жан-Пьер Жёне снял «Амели».",
        ["Амели"],
        ["Жан-Пьер Жёне"],
        "… снял «…».",
    ),
]


@pytest.mark.parametrize(("plot", "titles", "persons", "expected"), MASK_GOLDEN)
def test_mask_plot_golden(plot: str, titles: list[str], persons: list[str], expected: str) -> None:
    assert mask_plot(plot, titles=titles, person_names=persons) == expected


# Те же векторы, что у media-pipeline (tests/unit/test_stage3a_generators.py).
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("🎬🚀", ["🎬", "🚀"]),
        ("👨‍👩‍👧", ["👨‍👩‍👧"]),
        ("👍🏽❤️", ["👍🏽", "❤️"]),
        ("🇷🇺🇺🇸", ["🇷🇺", "🇺🇸"]),
        ("1️⃣й", ["1️⃣", "й"]),
        ("", []),
    ],
)
def test_graphemes(text: str, expected: list[str]) -> None:
    assert graphemes(text) == expected


@given(st.text(max_size=40))
def test_graphemes_partition_text(text: str) -> None:
    assert "".join(graphemes(text)) == text


def test_emoji_limited_to_12_graphemes() -> None:
    assert validate_emoji(" 👨‍👩‍👧" * 1) == "👨‍👩‍👧"
    assert validate_emoji("🚀" * 12) == "🚀" * 12
    with pytest.raises(DomainError) as exc:
        validate_emoji("🚀" * 13)
    assert exc.value.code == "catalog.clue_text_invalid_emoji"
    with pytest.raises(DomainError):
        validate_emoji("🚀 🌌")
    assert validate_emoji("   ") is None


def test_edit_creates_draft_revision_and_checks_base() -> None:
    texts = ClueTexts(ITEM)
    assert texts.edit(plot_masked="Сюжет …", tagline=None, emoji="🚀", base_revision=0)
    assert (texts.revision, texts.status) == (1, ClueTextStatus.DRAFT)
    assert not texts.edit(plot_masked="Сюжет …", tagline=None, emoji="🚀", base_revision=1)
    with pytest.raises(ConflictError) as exc:
        texts.edit(plot_masked="x", tagline=None, emoji=None, base_revision=0)
    assert exc.value.code == "catalog.clue_texts_revision_mismatch"
    with pytest.raises(DomainError):
        texts.edit(plot_masked="x" * 2001, tagline=None, emoji=None, base_revision=1)


def test_approve_then_edit_returns_to_draft_keeping_approved_revision() -> None:
    texts = ClueTexts(ITEM)
    with pytest.raises(DomainError):
        texts.approve(0)  # нечего одобрять
    texts.edit(plot_masked="Сюжет …", tagline="Слоган", emoji=None, base_revision=0)
    with pytest.raises(ConflictError):
        texts.approve(2)
    approved = texts.approve(1)
    assert approved == ApprovedClueTexts("Сюжет …", "Слоган", None)
    assert (texts.status, texts.approved_revision) == (ClueTextStatus.APPROVED, 1)
    texts.edit(plot_masked="Новый …", tagline="Слоган", emoji=None, base_revision=1)
    assert (texts.status, texts.revision, texts.approved_revision) == (
        ClueTextStatus.DRAFT,
        2,
        1,
    )


def test_import_fills_only_empty_fields_unless_authoritative() -> None:
    texts = ClueTexts(ITEM)
    texts.edit(plot_masked="Правка модератора", tagline=None, emoji=None, base_revision=0)
    texts.approve(1)
    assert texts.apply_import(
        plot_raw="Сырой сюжет", plot_masked="Авто", tagline="Слоган", emoji=None,
        authoritative=False,
    )  # fmt: skip
    assert (texts.plot_raw, texts.plot_masked, texts.tagline) == (
        "Сырой сюжет",
        "Правка модератора",
        "Слоган",
    )
    assert texts.status is ClueTextStatus.DRAFT  # новый слоган ждёт одобрения
    texts.apply_import(
        plot_raw=None, plot_masked="Засев", tagline=None, emoji="🚀", authoritative=True
    )
    assert (texts.plot_masked, texts.tagline, texts.emoji) == ("Засев", None, "🚀")


def _film() -> MediaItem:
    return MediaItem.create(
        ITEM,
        ItemData(kind=MediaKind.FILM, title="Интерстеллар", countries=("us", "GB", "US")),
        TitleNormalizer(),
        current_year=2026,
    )


def test_snapshot_publishes_countries_and_only_approved_texts() -> None:
    item = _film()
    snapshot = item.snapshot()
    assert snapshot["countries"] == ["US", "GB"]
    assert "clue_texts" not in snapshot
    item.pull_events()
    assert item.set_clue_texts(ApprovedClueTexts(plot_masked="Сюжет …", emoji="🚀"))
    (event,) = item.pull_events()
    payload = event.payload()  # type: ignore[attr-defined]  # снимковое событие
    assert payload["clue_texts"] == {"plot_masked": "Сюжет …", "emoji": "🚀"}
    assert payload["changed_fields"] == ["clue_texts"]
    assert payload["version"] == 2
    assert not item.set_clue_texts(ApprovedClueTexts(plot_masked="Сюжет …", emoji="🚀"))


def test_republish_bumps_version_without_changes() -> None:
    item = _film()
    item.pull_events()
    item.republish()
    (event,) = item.pull_events()
    payload = event.payload()  # type: ignore[attr-defined]
    assert (payload["version"], payload["changed_fields"]) == (2, ["republish"])
    assert payload["title"] == "Интерстеллар"


def test_countries_must_be_iso2() -> None:
    assert normalize_countries(["ru", " us "]) == ("RU", "US")
    with pytest.raises(DomainError):
        normalize_countries(["Russia"])
