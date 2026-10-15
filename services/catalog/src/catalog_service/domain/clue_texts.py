"""Тексты загадок ``description``/``emoji`` (ADR 0024): черновик → одобрение модератором.

Сюжет из TMDB (``plot_raw``) маскируется автоматически (``mask_plot``: название, алиасы,
имена персон → «…»), но наружу — в ``catalog.MediaItemCreated/Updated.clue_texts`` —
попадает только **одобренный** снимок (``ApprovedClueTexts``). Правка после одобрения
возвращает тексты в черновик, а в событиях остаётся прошлый одобренный снимок, пока
модератор не одобрит новую ревизию (новое одобрение — новое поколение ассетов в
media-pipeline).
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from gv_core.errors import ConflictError, DomainError

MAX_PLOT_RAW = 5000
MAX_PLOT = 2000
MAX_TAGLINE = 300
MAX_EMOJI_CHARS = 64
MAX_EMOJI_GRAPHEMES = 12
MASK = "…"

# Короче — только целое слово (иначе «Он» замаскировал бы «Она»); длиннее — со
# «словоизменительным хвостом» до 3 букв: «Нолан» → «Нолана», «Интерстеллар» → «…е».
_STEM_MIN = 4
_SUFFIX = r"\w{0,3}"
_MIN_PHRASE = 2


# ---------------------------------------------------------------- графемы
_REGIONAL = range(0x1F1E6, 0x1F200)
_SKIN_TONES = range(0x1F3FB, 0x1F400)
_TAGS = range(0xE0020, 0xE0080)
_VARIATION = range(0xFE00, 0xFE10)
_ZWJ = 0x200D
_KEYCAP = 0x20E3


def _extends(ch: str) -> bool:
    cp = ord(ch)
    return (
        unicodedata.category(ch) in ("Mn", "Me", "Mc")
        or cp in _VARIATION
        or cp in _SKIN_TONES
        or cp in _TAGS
        or cp == _KEYCAP
    )


def graphemes(text: str) -> list[str]:
    """Графемы строки (упрощённые расширенные кластеры UAX #29, достаточно для эмодзи).

    Тот же алгоритм, что у media-pipeline (``media_pipeline_service.domain.texts``): лимит
    12 графем здесь и «первые N эмодзи» уровня там считаются одинаково (golden-векторы в
    тестах обоих сервисов).
    """
    result: list[str] = []
    i, n = 0, len(text)
    while i < n:
        start = i
        first = text[i]
        i += 1
        if ord(first) in _REGIONAL and i < n and ord(text[i]) in _REGIONAL:
            i += 1
        while i < n:
            if ord(text[i]) == _ZWJ and i + 1 < n:
                i += 2
                continue
            if _extends(text[i]):
                i += 1
                continue
            break
        result.append(text[start:i])
    return result


# ---------------------------------------------------------------- маскировка
def _phrase_pattern(phrase: str) -> str:
    words = phrase.split()
    body = r"\s+".join(re.escape(w) for w in words)
    tail = _SUFFIX if len(words[-1]) >= _STEM_MIN else ""
    return rf"(?<!\w){body}{tail}(?!\w)"


def mask_plot(text: str, *, titles: Iterable[str], person_names: Iterable[str]) -> str:
    """Заменить в сюжете название, алиасы и имена персон на «…».

    Фразы ищутся без учёта регистра, целыми словами; у длинных слов допускается окончание
    (до трёх букв). Имена персон маскируются целиком и по частям (фамилия отдельно), части
    короче четырёх букв не маскируются. Подряд идущие маски схлопываются.

    Маскировка — черновик: модератор проверяет и правит текст перед одобрением.
    """
    phrases: set[str] = set()
    for title in titles:
        cleaned = " ".join(title.split())
        if len(cleaned) >= _MIN_PHRASE:
            phrases.add(cleaned)
    for name in person_names:
        cleaned = " ".join(name.split())
        if len(cleaned) >= _MIN_PHRASE:
            phrases.add(cleaned)
        phrases.update(part for part in cleaned.replace("-", " ").split() if len(part) >= _STEM_MIN)
    result = text
    # Длинные фразы раньше коротких: «Кристофер Нолан» маскируется целиком, а не по частям.
    for phrase in sorted(phrases, key=lambda p: (-len(p), p)):
        result = re.sub(_phrase_pattern(phrase), MASK, result, flags=re.IGNORECASE)
    return re.sub(rf"{MASK}(?:[\s,]*{MASK})+", MASK, result)


# ---------------------------------------------------------------- проверки
def _clean(value: str | None, limit: int, field: str) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > limit:
        raise DomainError(f"{field}: длиннее {limit} символов", code="catalog.clue_text_too_long")
    return cleaned


def validate_emoji(value: str | None) -> str | None:
    """Эмодзи-описание: до 64 символов и до 12 графем, без пробелов внутри."""
    cleaned = _clean(value, MAX_EMOJI_CHARS, "emoji")
    if cleaned is None:
        return None
    if any(ch.isspace() for ch in cleaned):
        raise DomainError("emoji: без пробелов", code="catalog.clue_text_invalid_emoji")
    if len(graphemes(cleaned)) > MAX_EMOJI_GRAPHEMES:
        raise DomainError(
            f"emoji: больше {MAX_EMOJI_GRAPHEMES} эмодзи", code="catalog.clue_text_invalid_emoji"
        )
    return cleaned


# ---------------------------------------------------------------- одобренный снимок
@dataclass(frozen=True, slots=True)
class ApprovedClueTexts:
    """Одобренные тексты — то, что видят потребители событий (``clue_texts``)."""

    plot_masked: str | None = None
    tagline: str | None = None
    emoji: str | None = None

    @property
    def is_empty(self) -> bool:
        """Нечего публиковать."""
        return not (self.plot_masked or self.tagline or self.emoji)

    def to_contract(self) -> dict[str, str]:
        """Только заданные поля (в контракте они необязательные и непустые)."""
        data = {"plot_masked": self.plot_masked, "tagline": self.tagline, "emoji": self.emoji}
        return {k: v for k, v in data.items() if v}

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> ApprovedClueTexts:
        """Из JSONB строки единицы."""
        data = data or {}
        return cls(
            plot_masked=data.get("plot_masked") or None,
            tagline=data.get("tagline") or None,
            emoji=data.get("emoji") or None,
        )


class ClueTextStatus(StrEnum):
    """Состояние текстов: черновик ждёт одобрения модератора."""

    DRAFT = "draft"
    APPROVED = "approved"


# ---------------------------------------------------------------- черновик
class ClueTexts:
    """Тексты загадок единицы с ревизиями.

    Attributes:
        media_item_id: Единица каталога.
        plot_raw: Исходный сюжет (TMDB ``overview`` или файл импорта) — только для модератора.
        plot_masked: Сюжет с маскировкой (черновик или одобренный).
        tagline: Слоган.
        emoji: Эмодзи-описание (до 12 графем).
        status: ``draft`` | ``approved``.
        revision: Номер ревизии текущих текстов (0 — текстов ещё не было); растёт на
            каждой правке, одобряется конкретная ревизия.
        approved_revision: Какая ревизия одобрена последней (``None`` — ни одной).
        persisted: Загружен из хранилища (иначе — новый).
    """

    def __init__(
        self,
        media_item_id: uuid.UUID,
        *,
        plot_raw: str | None = None,
        plot_masked: str | None = None,
        tagline: str | None = None,
        emoji: str | None = None,
        status: ClueTextStatus = ClueTextStatus.DRAFT,
        revision: int = 0,
        approved_revision: int | None = None,
        persisted: bool = False,
    ) -> None:
        self.media_item_id = media_item_id
        self.plot_raw = plot_raw
        self.plot_masked = plot_masked
        self.tagline = tagline
        self.emoji = emoji
        self.status = status
        self.revision = revision
        self.approved_revision = approved_revision
        self.persisted = persisted

    def _current(self) -> ApprovedClueTexts:
        return ApprovedClueTexts(self.plot_masked, self.tagline, self.emoji)

    def edit(
        self,
        *,
        plot_masked: str | None,
        tagline: str | None,
        emoji: str | None,
        base_revision: int,
    ) -> bool:
        """Правка модератора (все три поля целиком; ``None`` — очистить).

        Returns:
            ``True`` — тексты изменились (новая ревизия, статус ``draft``).

        Raises:
            ConflictError: ``catalog.clue_texts_revision_mismatch`` — тексты изменили
                после того, как модератор их открыл.
            DomainError: длина, эмодзи больше 12.
        """
        if base_revision != self.revision:
            raise ConflictError(
                "тексты изменились, обновите карточку",
                code="catalog.clue_texts_revision_mismatch",
                details={"revision": self.revision},
            )
        new = ApprovedClueTexts(
            _clean(plot_masked, MAX_PLOT, "plot_masked"),
            _clean(tagline, MAX_TAGLINE, "tagline"),
            validate_emoji(emoji),
        )
        if new == self._current():
            return False
        self.plot_masked, self.tagline, self.emoji = new.plot_masked, new.tagline, new.emoji
        self.revision += 1
        self.status = ClueTextStatus.DRAFT
        return True

    def approve(self, revision: int) -> ApprovedClueTexts:
        """Одобрить ревизию ``revision`` и вернуть снимок для событий.

        Raises:
            ConflictError: ревизия не совпадает с текущей.
            DomainError: ``catalog.clue_texts_empty`` — одобрять нечего.
        """
        if revision != self.revision:
            raise ConflictError(
                "одобряется не текущая ревизия текстов",
                code="catalog.clue_texts_revision_mismatch",
                details={"revision": self.revision},
            )
        approved = self._current()
        if approved.is_empty:
            raise DomainError("нет текстов для одобрения", code="catalog.clue_texts_empty")
        self.status = ClueTextStatus.APPROVED
        self.approved_revision = revision
        return approved

    def apply_import(
        self,
        *,
        plot_raw: str | None,
        plot_masked: str | None,
        tagline: str | None,
        emoji: str | None,
        authoritative: bool,
    ) -> bool:
        """Слить тексты импорта.

        Обычный импорт (TMDB) обновляет ``plot_raw`` и заполняет только пустые поля —
        правки модератора он не перетирает; новые тексты требуют одобрения (``draft``).
        ``authoritative`` (синтетический засев в local/ci) заменяет тексты целиком.

        Returns:
            ``True`` — что-то изменилось.
        """
        raw = _clean(plot_raw, MAX_PLOT_RAW, "plot_raw")
        masked = _clean(plot_masked, MAX_PLOT, "plot_masked")
        tag = _clean(tagline, MAX_TAGLINE, "tagline")
        emo = validate_emoji(emoji)
        before = (self.plot_raw, self._current())
        if raw is not None:
            self.plot_raw = raw
        if authoritative:
            fields = ApprovedClueTexts(masked, tag, emo)
        else:
            fields = ApprovedClueTexts(
                self.plot_masked or masked, self.tagline or tag, self.emoji or emo
            )
        if fields != self._current():
            self.plot_masked, self.tagline, self.emoji = (
                fields.plot_masked,
                fields.tagline,
                fields.emoji,
            )
            self.revision += 1
            self.status = ClueTextStatus.DRAFT
        return (self.plot_raw, self._current()) != before
