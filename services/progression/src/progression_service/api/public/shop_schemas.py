"""Схемы публичного API 5b: магазин, инвентарь, оформление, промокод, аватарка (ADR 0045, 0048).

Слоты, редкости, цены и условия открытия приходят из данных (таблица предметов и конфиг
progression), в коде значений нет. Цену и условия клиент не передаёт - только id предмета.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Key = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]
MediaKind = Literal["static", "animated"]
ItemState = Literal["locked", "available", "owned"]
InventorySource = Literal["purchase", "level", "wheel", "promo", "pack", "admin", "event"]
AvatarRequestStatus = Literal["pending", "approved", "rejected"]
PromoRewardKind = Literal["verses", "item"]


class _Request(BaseModel):
    """Тело запроса: лишние поля (цена, награда от клиента) - 422, а не молчаливо игнорируются."""

    model_config = ConfigDict(extra="forbid")


class ItemView(BaseModel):
    """Предмет косметики."""

    id: UUID
    key: Key
    slot: Key
    rarity: Key
    title: str = Field(max_length=80)
    media_kind: MediaKind
    asset_url: str = Field(description="Публичный адрес картинки или анимации (animated WebP)")
    poster_url: str | None = Field(
        description="Статичный кадр анимации (для prefers-reduced-motion); у static - null"
    )
    level_required: int | None = Field(
        ge=1, description="Открывается с уровня; null - по уровню нет"
    )
    price: int | None = Field(ge=1, description="Цена в Версах; null - не продаётся")
    event_key: Key | None = Field(description="Сезонное событие (5d); в 5b всегда null")


class ShopItem(BaseModel):
    """Предмет витрины с состоянием для игрока."""

    item: ItemView
    state: ItemState = Field(
        description="locked - не хватает уровня и цены; available - можно получить; owned - есть"
    )


class ShopCatalog(BaseModel):
    """Витрина: активные предметы, можно отфильтровать по слоту."""

    items: list[ShopItem]
    balance: int = Field(ge=0, description="Версы игрока (бакет earned)")


class PurchaseResult(BaseModel):
    """Результат покупки. Повтор с тем же `Idempotency-Key` возвращает ту же выдачу."""

    item: ItemView
    balance_after: int = Field(ge=0)
    repeated: bool = Field(description="true - повтор уже выполненной покупки")


class InventoryEntry(BaseModel):
    """Предмет в инвентаре."""

    item: ItemView
    source: InventorySource
    acquired_at: datetime
    expires_at: datetime | None = Field(description="Временный предмет (5d); в 5b всегда null")
    equipped: bool


class MyInventory(BaseModel):
    """Инвентарь игрока (просроченные предметы скрыты)."""

    items: list[InventoryEntry]


class AppearanceSlot(BaseModel):
    """Слот оформления."""

    slot: Key
    item: ItemView | None = Field(description="null - слот пуст")


class Appearance(BaseModel):
    """Оформление профиля: по одному предмету на слот."""

    slots: list[AppearanceSlot]


class EquipRequest(_Request):
    """Надеть предмет в слот или снять (`item_id = null`)."""

    item_id: UUID | None = Field(
        description="null - снять; предмет должен быть в инвентаре и подходить слоту"
    )


class PromoRedeemRequest(_Request):
    """Погасить промокод."""

    code: str = Field(
        min_length=1,
        max_length=64,
        description="Код как ввёл игрок (регистр и пробелы нормализуются)",
    )


class PromoRedeemResult(BaseModel):
    """Награда промокода. Повтор погашения того же кода возвращает ту же награду."""

    reward_kind: PromoRewardKind
    amount: int | None = Field(ge=1, description="Версы; для предмета null")
    item: ItemView | None
    repeated: bool


class AvatarLimits(BaseModel):
    """Лимиты загрузки (из конфига media-pipeline)."""

    max_bytes: int = Field(ge=1)
    formats: list[Key]
    min_side_px: int = Field(ge=1)
    max_side_px: int = Field(ge=1)


class AvatarRequestView(BaseModel):
    """Заявка на свою аватарку."""

    asset_id: UUID
    status: AvatarRequestStatus
    submitted_at: datetime
    reason_code: Key | None = Field(description="Код причины отклонения; иначе null")


class MyAvatar(BaseModel):
    """Своя аватарка: право, действующий аватар, последняя заявка."""

    entitled: bool = Field(description="Куплен товар «своя аватарка»")
    current_url: str | None = Field(
        description="Одобренная аватарка; null - используется предмет слота"
    )
    request: AvatarRequestView | None = Field(description="Последняя заявка; null - не подавалась")
    limits: AvatarLimits


class PackView(BaseModel):
    """Пак косметики (скрыт при `billing.enabled=false`)."""

    id: UUID
    key: Key
    title: str = Field(max_length=80)
    style: Key
    items: list[ItemView]
    verses_amount: int = Field(ge=0)
    price_kopecks: int = Field(ge=1, description="Цена в копейках")


class PlayerAppearance(BaseModel):
    """Оформление игрока (публичное): рамка, титул, фон, аватар."""

    user_id: UUID
    appearance: Appearance
    custom_avatar_url: str | None = Field(
        description="Одобренная своя аватарка; null - аватар из слота"
    )


class PlayersAppearance(BaseModel):
    """Оформление нескольких игроков (матч, профиль); неизвестные id пропускаются."""

    players: list[PlayerAppearance]


class PackCatalog(BaseModel):
    """Витрина паков."""

    packs: list[PackView]
