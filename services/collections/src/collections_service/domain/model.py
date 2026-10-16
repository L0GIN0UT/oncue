"""Значения домена сборников: перечисления, тело черновика, опубликованная версия (D1)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class Domain(StrEnum):
    """Домен сборника: единицы только этого домена (смешанных сборников в 3b нет)."""

    MUSIC = "music"
    FILM = "film"


class Kind(StrEnum):
    """Пользовательский или кураторский («Guessverse», D14) сборник."""

    UGC = "ugc"
    CURATED = "curated"


class Status(StrEnum):
    """Статус агрегата.

    ``draft`` — ещё ни разу не опубликован; ``published`` — есть опубликованная версия
    (поверх неё может лежать черновик); ``hidden`` — скрыт модератором; ``deleted`` —
    удалён (никому не отдаётся).
    """

    DRAFT = "draft"
    PUBLISHED = "published"
    HIDDEN = "hidden"
    DELETED = "deleted"


class Visibility(StrEnum):
    """``unlisted`` — играется по ссылке, но не попадает в каталог."""

    PUBLIC = "public"
    UNLISTED = "unlisted"


@dataclass(frozen=True, slots=True)
class Item:
    """Единица сборника: ``media_item_id`` каталога и свои эмодзи автора (ADR 0029)."""

    media_item_id: UUID
    emoji: str | None = None


@dataclass(frozen=True, slots=True)
class DraftBody:
    """Тело черновика — всё, что автор правит в конструкторе (домен фиксирован).

    Черновик хранится как прислан (в пределах схемы API) и может быть неполным; текст
    нормализуется и проверяется при публикации (``policy.CollectionPolicy``).
    """

    title: str
    description: str | None = None
    tags: tuple[str, ...] = ()
    cover: str | None = None
    default_mode_key: str | None = None
    visibility: Visibility = Visibility.PUBLIC
    items: tuple[Item, ...] = ()

    def to_json(self) -> dict[str, Any]:
        """JSON тела (хранение и ответ API — форма ``DraftBody`` схемы)."""
        return {
            "title": self.title,
            "description": self.description,
            "tags": list(self.tags),
            "cover": {"preset": self.cover} if self.cover else None,
            "default_mode_key": self.default_mode_key,
            "visibility": str(self.visibility),
            "items": [
                {
                    "media_item_id": str(i.media_item_id),
                    "custom": {"emoji": i.emoji} if i.emoji is not None else None,
                }
                for i in self.items
            ],
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> DraftBody:
        """Тело из JSON (хранилище или запрос, уже прошедший схему API)."""
        cover = raw.get("cover") or None
        return cls(
            title=str(raw["title"]),
            description=raw.get("description"),
            tags=tuple(raw.get("tags") or ()),
            cover=cover["preset"] if cover else None,
            default_mode_key=raw.get("default_mode_key"),
            visibility=Visibility(raw.get("visibility", Visibility.PUBLIC)),
            items=tuple(
                Item(
                    media_item_id=UUID(str(i["media_item_id"])),
                    emoji=(i.get("custom") or {}).get("emoji"),
                )
                for i in raw.get("items") or ()
            ),
        )


@dataclass(frozen=True, slots=True)
class CollectionBody:
    """Проверенное содержимое опубликованной версии (неизменяемый снимок, D1).

    Текст уже нормализован ``gv_text.ugc``, теги и пресет — из словаря ``meta``, единицы —
    активные единицы домена сборника без повторов.
    """

    title: str
    description: str | None
    tags: tuple[str, ...]
    cover: str | None
    default_mode_key: str
    visibility: Visibility
    items: tuple[Item, ...]

    def to_json(self) -> dict[str, Any]:
        """JSON версии (``collection_versions.body``)."""
        return {
            "title": self.title,
            "description": self.description,
            "tags": list(self.tags),
            "cover": self.cover,
            "default_mode_key": self.default_mode_key,
            "visibility": str(self.visibility),
            "items": [
                {"media_item_id": str(i.media_item_id), "emoji": i.emoji} for i in self.items
            ],
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> CollectionBody:
        """Версия из JSON хранилища."""
        return cls(
            title=str(raw["title"]),
            description=raw.get("description"),
            tags=tuple(raw.get("tags") or ()),
            cover=raw.get("cover"),
            default_mode_key=str(raw["default_mode_key"]),
            visibility=Visibility(raw["visibility"]),
            items=tuple(
                Item(media_item_id=UUID(str(i["media_item_id"])), emoji=i.get("emoji"))
                for i in raw.get("items") or ()
            ),
        )

    def as_draft(self) -> DraftBody:
        """Тело черновика поверх этой версии (``GET .../draft`` без черновика)."""
        return DraftBody(
            title=self.title,
            description=self.description,
            tags=self.tags,
            cover=self.cover,
            default_mode_key=self.default_mode_key,
            visibility=self.visibility,
            items=self.items,
        )

    def without(self, media_item_id: UUID) -> CollectionBody:
        """Та же версия без единицы (позиции сдвигаются, порядок сохраняется)."""
        return CollectionBody(
            title=self.title,
            description=self.description,
            tags=self.tags,
            cover=self.cover,
            default_mode_key=self.default_mode_key,
            visibility=self.visibility,
            items=tuple(i for i in self.items if i.media_item_id != media_item_id),
        )


@dataclass(frozen=True, slots=True)
class Draft:
    """Единственный черновик сборника (D2): ревизия растёт монотонно у агрегата."""

    revision: int
    base_version: int | None
    body: DraftBody
    client_label: str | None
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Hidden:
    """Кто и почему скрыл сборник."""

    reason: str
    actor_id: UUID | None
    at: datetime
