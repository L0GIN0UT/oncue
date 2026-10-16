"""Правила содержимого сборника: UGC-текст, словари ``meta``, состав и эмодзи (K3, D8, ADR 0030).

Политика чистая: состояние единиц каталога приходит аргументом (``ItemInfo`` из проекции
``catalog_items_proj``), лимиты — из ``GV_COLLECTIONS__LIMITS__*``, словари тегов и пресетов
обложек — из конфига ``meta``. Ошибки публикации собираются все сразу (``[{path, code}]``),
чтобы конструктор подсветил каждую.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from collections_service.domain.errors import (
    DraftInvalidError,
    PublishInvalidError,
    TitleInvalidError,
)
from collections_service.domain.model import CollectionBody, Domain, DraftBody, Item
from gv_text.emoji import graphemes, is_strict_emoji
from gv_text.ugc import UgcPolicy, UgcViolation

ITEMS_TOO_FEW = "collections.items_too_few"
ITEMS_TOO_MANY = "collections.items_too_many"
ITEM_DUPLICATE = "collections.item_duplicate"
ITEM_DOMAIN_MISMATCH = "collections.item_domain_mismatch"
ITEM_UNAVAILABLE = "collections.item_unavailable"
MODE_INVALID = "collections.mode_invalid"
EMOJI_INVALID = "collections.emoji_invalid"
EMOJI_TOO_SHORT = "collections.emoji_too_short"
TAG_UNKNOWN = "collections.tag_unknown"
TAGS_TOO_MANY = "collections.tags_too_many"
COVER_UNKNOWN = "collections.cover_unknown"
TITLE_INVALID = "collections.title_invalid"
DESCRIPTION_INVALID = "collections.description_invalid"

ACTIVE = "active"


@dataclass(frozen=True, slots=True)
class CoverPreset:
    """Пресет обложки (D6: постеров нет): градиент CSS-цветов и эмодзи."""

    id: str
    gradient: tuple[str, ...]
    emoji: str


@dataclass(frozen=True, slots=True)
class Tag:
    """Тег словаря: ключ, ключ перевода, домены и слова для поиска (D10)."""

    key: str
    title_key: str
    domains: tuple[Domain, ...]
    search_terms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MetaConfig:
    """Словари конструктора (конфиг ``GV_COLLECTIONS__META_CONFIG``)."""

    cover_presets: tuple[CoverPreset, ...]
    tags: tuple[Tag, ...]

    def tag(self, key: str) -> Tag | None:
        """Тег по ключу."""
        return next((t for t in self.tags if t.key == key), None)

    def has_cover(self, preset: str) -> bool:
        """Есть ли пресет обложки."""
        return any(p.id == preset for p in self.cover_presets)

    def search_terms(self, tags: Sequence[str]) -> list[str]:
        """Слова поиска тегов сборника (ключ + ``search_terms``)."""
        terms: list[str] = []
        for key in tags:
            tag = self.tag(key)
            terms.append(key.replace("_", " "))
            if tag is not None:
                terms.extend(tag.search_terms)
        return terms


@dataclass(frozen=True, slots=True)
class Limits:
    """Лимиты содержимого (подмножество ``CollectionsLimits`` настроек)."""

    title_max: int
    description_max: int
    items_min: int
    items_max: int
    tags_max: int
    emoji_max_graphemes: int
    # Профиль ``emoji`` media-pipeline не строит набор из меньшего числа графем (ADR 0029).
    emoji_min_graphemes: int = 1


@dataclass(frozen=True, slots=True)
class ItemInfo:
    """Единица каталога из проекции: домен и статус (``active`` | ``blocked``)."""

    domain: str
    status: str


def _error(path: str, code: str, **extra: Any) -> dict[str, Any]:
    return {"path": path, "code": code, **extra}


class CollectionPolicy:
    """Проверка названия, черновика и публикации.

    Args:
        limits: Лимиты из настроек.
        ugc: Политика UGC-текста (denylist из ``GV_UGC__DENYLIST_PATH``).
        meta: Словари тегов и пресетов обложек.
    """

    def __init__(self, limits: Limits, ugc: UgcPolicy, meta: MetaConfig) -> None:
        self.limits = limits
        self.ugc = ugc
        self.meta = meta

    # ------------------------------------------------------------------ текст
    def title(self, raw: str) -> str:
        """Нормализованное название (ссылки, ``@`` и e-mail запрещены).

        Raises:
            TitleInvalidError: ``details.reason`` — код ``ugc.*``.
        """
        try:
            return self.ugc.check(raw, max_len=self.limits.title_max, forbid_links=True)
        except UgcViolation as exc:
            raise TitleInvalidError(
                "название не подходит", details={"reason": exc.code, **exc.details}
            ) from exc

    def _description(self, raw: str | None) -> tuple[str | None, str | None]:
        """Нормализованное описание (пустое → ``None``) и код нарушения ``ugc.*``."""
        if raw is None or not raw.strip():
            return None, None
        try:
            text = self.ugc.check(raw, max_len=self.limits.description_max, multiline=True)
        except UgcViolation as exc:
            if exc.code == "ugc.empty":
                return None, None
            return None, exc.code
        return text, None

    # ------------------------------------------------------------------ черновик
    def check_draft(self, body: DraftBody) -> None:
        """Лимиты черновика (неполный черновик допустим, D2).

        Raises:
            DraftInvalidError: единиц больше ``items_max`` или тегов больше ``tags_max``.
        """
        errors: list[dict[str, Any]] = []
        if len(body.items) > self.limits.items_max:
            errors.append(_error("items", ITEMS_TOO_MANY, max=self.limits.items_max))
        if len(set(body.tags)) > self.limits.tags_max:
            errors.append(_error("tags", TAGS_TOO_MANY, max=self.limits.tags_max))
        if errors:
            raise DraftInvalidError("черновик нарушает лимиты", details={"errors": errors})

    # ------------------------------------------------------------------ публикация
    def publishable(
        self, domain: Domain, body: DraftBody, items: Mapping[UUID, ItemInfo]
    ) -> CollectionBody:
        """Проверить тело для публикации и вернуть нормализованную версию.

        Args:
            domain: Домен сборника.
            body: Тело черновика (или кураторского сборника).
            items: Проекция каталога по единицам тела (нет в словаре — неизвестна).

        Raises:
            PublishInvalidError: ``details.errors`` — все нарушения ``[{path, code}]``.
        """
        errors: list[dict[str, Any]] = []
        title = self._checked_title(body.title, errors)
        description, reason = self._description(body.description)
        if reason is not None:
            errors.append(_error("description", DESCRIPTION_INVALID, reason=reason))
        tags = self._checked_tags(domain, body.tags, errors)
        if body.cover is not None and not self.meta.has_cover(body.cover):
            errors.append(_error("cover.preset", COVER_UNKNOWN))
        mode = body.default_mode_key
        if mode is None or not re.fullmatch(rf"{domain}\.collection\.[a-z0-9_]+", mode):
            errors.append(_error("default_mode_key", MODE_INVALID))
        checked = self._checked_items(domain, body.items, items, errors)
        if errors:
            raise PublishInvalidError(errors)
        assert mode is not None  # noqa: S101 — проверено выше
        return CollectionBody(
            title=title,
            description=description,
            tags=tags,
            cover=body.cover,
            default_mode_key=mode,
            visibility=body.visibility,
            items=checked,
        )

    def _checked_title(self, raw: str, errors: list[dict[str, Any]]) -> str:
        try:
            return self.title(raw)
        except TitleInvalidError as exc:
            errors.append(_error("title", TITLE_INVALID, reason=exc.details["reason"]))
            return ""

    def _checked_tags(
        self, domain: Domain, raw: Sequence[str], errors: list[dict[str, Any]]
    ) -> tuple[str, ...]:
        tags = tuple(dict.fromkeys(raw))
        if len(tags) > self.limits.tags_max:
            errors.append(_error("tags", TAGS_TOO_MANY, max=self.limits.tags_max))
        for index, key in enumerate(tags):
            tag = self.meta.tag(key)
            if tag is None or domain not in tag.domains:
                errors.append(_error(f"tags[{index}]", TAG_UNKNOWN))
        return tags

    def _checked_items(
        self,
        domain: Domain,
        raw: Sequence[Item],
        known: Mapping[UUID, ItemInfo],
        errors: list[dict[str, Any]],
    ) -> tuple[Item, ...]:
        limits = self.limits
        if len(raw) < limits.items_min:
            errors.append(_error("items", ITEMS_TOO_FEW, min=limits.items_min))
        if len(raw) > limits.items_max:
            errors.append(_error("items", ITEMS_TOO_MANY, max=limits.items_max))
        seen: set[UUID] = set()
        for index, item in enumerate(raw):
            path = f"items[{index}]"
            if item.media_item_id in seen:
                errors.append(_error(path, ITEM_DUPLICATE))
            seen.add(item.media_item_id)
            info = known.get(item.media_item_id)
            if info is None or info.status != ACTIVE:
                errors.append(_error(path, ITEM_UNAVAILABLE))
            elif info.domain != domain:
                errors.append(_error(path, ITEM_DOMAIN_MISMATCH))
            if item.emoji is None:
                continue
            if not is_strict_emoji(item.emoji, max_graphemes=limits.emoji_max_graphemes):
                errors.append(_error(f"{path}.custom.emoji", EMOJI_INVALID))
            elif len(graphemes(item.emoji)) < limits.emoji_min_graphemes:
                errors.append(
                    _error(f"{path}.custom.emoji", EMOJI_TOO_SHORT, min=limits.emoji_min_graphemes)
                )
        return tuple(raw)
