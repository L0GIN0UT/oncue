"""Схемы admin API 5b: предметы, паки, промокоды (ADR 0045, 0046, 0048).

Слоты и редкости проверяются по действующему конфигу progression (секция ``shop``), в схеме
они ключи. Коды промокодов в ответах списков не возвращаются: сервис хранит только хеш,
открытый код показывается один раз - в ответе на создание.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from gv_service import Reason
from progression_service.api.public.shop_schemas import Key, MediaKind, PromoRewardKind

Cursor = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{1,512}$")]
Title = Annotated[str, StringConstraints(min_length=1, max_length=80)]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ItemFields(_Request):
    """Поля предмета. Условие открытия: `level_required` и/или `price` (хотя бы одно)."""

    key: Key
    slot: Key
    rarity: Key
    title: Title
    media_kind: MediaKind
    level_required: int | None = Field(default=None, ge=1, le=1000)
    price: int | None = Field(default=None, ge=1, le=100_000_000)
    active: bool = Field(
        default=False, description="Активный предмет виден на витрине; нужна загруженная картинка"
    )
    event_key: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_]*$",
        max_length=64,
        description=(
            "Предмет только для награды сезонного события с этим ключом: без цены и уровня, "
            "не на витрине, не с колеса и не по промокоду (422 `progression.item_event_exclusive`)"
        ),
    )


class CreateItemRequest(ItemFields):
    """Создать предмет (без картинки - неактивным)."""

    reason: Reason


class UpdateItemRequest(ItemFields):
    """Заменить поля предмета (`key` менять нельзя - 422 `progression.item_key_immutable`)."""

    reason: Reason


class AdminItem(BaseModel):
    """Предмет в админке."""

    id: UUID
    key: Key
    slot: Key
    rarity: Key
    title: str
    media_kind: MediaKind
    level_required: int | None
    price: int | None
    active: bool
    event_key: str | None = Field(description="Предмет награды события; null - обычный")
    asset_url: str | None = Field(description="null - картинка не загружена")
    poster_url: str | None
    owners: int = Field(ge=0, description="Сколько игроков владеют предметом")
    created_at: datetime
    updated_at: datetime


class AdminItemPage(BaseModel):
    """Страница предметов (курсор)."""

    items: list[AdminItem]
    next_cursor: Cursor | None


class PackFields(_Request):
    """Пак косметики (5b: только модель и CRUD; продажа - вместе с billing, этап 6)."""

    key: Key
    title: Title
    style: Key
    item_ids: list[UUID] = Field(min_length=1, max_length=50)
    verses_amount: int = Field(default=0, ge=0, le=100_000_000)
    price_kopecks: int = Field(ge=1, le=100_000_000, description="Цена в копейках")
    active: bool = False


class SavePackRequest(PackFields):
    """Создать или заменить пак."""

    reason: Reason


class AdminPack(BaseModel):
    """Пак в админке."""

    id: UUID
    key: Key
    title: str
    style: Key
    item_ids: list[UUID]
    verses_amount: int
    price_kopecks: int
    active: bool
    created_at: datetime
    updated_at: datetime


class AdminPackList(BaseModel):
    """Паки."""

    items: list[AdminPack]


class PromoReward(_Request):
    """Награда: ровно одно из `verses_amount` и `item_id` (в соответствии с `kind`)."""

    kind: PromoRewardKind
    verses_amount: int | None = Field(default=None, ge=1, le=100_000_000)
    item_id: UUID | None = None


class CreatePromoRequest(_Request):
    """Сгенерировать партию кодов с общими условиями."""

    label: Title = Field(description="Название партии (для админки; в уведомления не попадает)")
    reward: PromoReward
    count: int = Field(ge=1, le=1000, description="Сколько кодов сгенерировать")
    max_uses: int = Field(ge=1, le=1_000_000, description="Лимит активаций на каждый код")
    per_user_once: bool = Field(default=True, description="Один код - один раз на игрока")
    expires_at: datetime | None = None
    reason: Reason


class CreatedPromo(BaseModel):
    """Партия кодов. `codes` показывается ОДИН раз - потом в БД только хеш."""

    id: UUID
    label: str
    codes: list[str]


class PromoBatch(BaseModel):
    """Партия кодов в списке (без самих кодов)."""

    id: UUID
    label: str
    reward: PromoReward
    codes_total: int = Field(ge=1)
    activations: int = Field(ge=0)
    max_uses: int
    per_user_once: bool
    expires_at: datetime | None
    status: Literal["active", "expired", "revoked"]
    created_at: datetime


class PromoBatchPage(BaseModel):
    """Страница партий (курсор)."""

    items: list[PromoBatch]
    next_cursor: Cursor | None


class RevokeRequest(_Request):
    """Отозвать партию: новые активации невозможны, выданные награды не отзываются."""

    reason: Reason
