"""Предмет косметики, покупка и проверки админки (ADR 0045, 0046).

Чистые правила без I/O: условия открытия, цена, слоты и редкости приходят из данных (таблица
предметов) и конфига (секция ``shop``); в коде значений нет.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from gv_core.errors import ConflictError, ForbiddenError, InvalidInputError, NotFoundError
from progression_service.domain.config import ConfigInvalidError, MediaLimits, ShopConfig

OBJECT_PREFIX = "c/"
"""Префикс объектов косметики в бакете (политика анонимного чтения и nginx смотрят на него)."""
_TOKEN = re.compile(r"^[0-9a-f]{32}$")
_STATIC_FORMATS = frozenset({"webp", "png"})
_ANIMATED_FORMATS = frozenset({"webp"})
_ANIMATED_MIN_FRAMES = 2


class MediaKind(StrEnum):
    """Способ показа: ``static`` - картинка, ``animated`` - animated WebP (ADR 0046)."""

    STATIC = "static"
    ANIMATED = "animated"


class ItemState(StrEnum):
    """Состояние предмета на витрине для игрока."""

    LOCKED = "locked"
    AVAILABLE = "available"
    OWNED = "owned"


class ItemNotFoundError(NotFoundError):
    """Предмета нет или он не продаётся (404)."""

    default_code = "progression.item_not_found"

    def __init__(self) -> None:
        super().__init__("предмет не найден")


class ItemNotAvailableError(ForbiddenError):
    """Предмет нельзя купить: у него нет цены, он открывается только уровнем (403)."""

    default_code = "progression.item_not_available"

    def __init__(self) -> None:
        super().__init__("предмет нельзя купить, он открывается уровнем")


class AlreadyOwnedError(ConflictError):
    """Предмет уже в инвентаре (409)."""

    default_code = "progression.already_owned"

    def __init__(self) -> None:
        super().__init__("предмет уже есть в инвентаре")


class InsufficientFundsError(ConflictError):
    """Не хватает Версов (409)."""

    default_code = "progression.insufficient_funds"

    def __init__(self) -> None:
        super().__init__("не хватает Версов")


class ItemSlotUnknownError(InvalidInputError):
    """Слота нет в секции ``shop`` действующего конфига (422)."""

    default_code = "progression.item_slot_unknown"

    def __init__(self) -> None:
        super().__init__("слот не описан в конфиге")


class ItemRarityUnknownError(InvalidInputError):
    """Редкости нет в секции ``shop`` действующего конфига (422)."""

    default_code = "progression.item_rarity_unknown"

    def __init__(self) -> None:
        super().__init__("редкость не описана в конфиге")


class ItemUnlockRequiredError(InvalidInputError):
    """Нужен ``level_required`` или ``price`` (422)."""

    default_code = "progression.item_unlock_required"

    def __init__(self) -> None:
        super().__init__("нужно условие открытия: уровень или цена")


class ItemKeyImmutableError(InvalidInputError):
    """Ключ предмета менять нельзя (422)."""

    default_code = "progression.item_key_immutable"

    def __init__(self) -> None:
        super().__init__("ключ предмета не меняется")


class ItemKeyTakenError(ConflictError):
    """Ключ предмета уже занят (409)."""

    default_code = "progression.item_key_taken"

    def __init__(self) -> None:
        super().__init__("предмет с таким ключом уже есть")


class ItemMediaMissingError(InvalidInputError):
    """Активировать предмет без картинки нельзя (422)."""

    default_code = "progression.item_media_missing"

    def __init__(self) -> None:
        super().__init__("сначала загрузите картинку предмета")


class ItemMediaInvalidError(InvalidInputError):
    """Файл не картинка нужного формата или превышает лимиты кадров, длительности, стороны (422)."""

    default_code = "progression.item_media_invalid"

    def __init__(self, reason: str) -> None:
        super().__init__(f"файл не подходит: {reason}")


class ItemMediaTooLargeError(InvalidInputError):
    """Файл больше лимита размера (на HTTP - 413, обрабатывается в ручке)."""

    default_code = "progression.item_media_too_large"

    def __init__(self) -> None:
        super().__init__("файл больше допустимого размера")


class CustomAvatarItemError(ConfigInvalidError):
    """Товар «своя аватарка» из конфига не существует или лежит в неизвестном слоте."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Item:
    """Предмет косметики (строка ``items``).

    Attributes:
        slot: Ключ слота из ``shop.slots`` конфига.
        rarity: Ключ редкости из ``shop.rarities``.
        asset_key: Ключ объекта в S3 (``c/<hex>.webp|png``); ``None`` - картинки нет.
        poster_key: Статичный первый кадр анимации; у ``static`` - ``None``.
        level_required: С какого уровня предмет выдаётся сам; ``None`` - не по уровню.
        price: Цена в Версах; ``None`` - не продаётся.
    """

    id: UUID
    key: str
    slot: str
    rarity: str
    title: str
    media_kind: MediaKind
    asset_key: str | None
    poster_key: str | None
    level_required: int | None
    price: int | None
    active: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MediaInfo:
    """Что узнали о файле декодированием (кадры, длительность, размеры)."""

    format: str
    width: int
    height: int
    frames: int
    duration_ms: int
    size_bytes: int


# ------------------------------------------------------------------ витрина и покупка
def item_state(item: Item, *, level: int, owned: bool) -> ItemState:
    """Состояние для игрока: есть, можно получить (уровнем или за Версы) или закрыт."""
    if owned:
        return ItemState.OWNED
    by_level = item.level_required is not None and level >= item.level_required
    if by_level or item.price is not None:
        return ItemState.AVAILABLE
    return ItemState.LOCKED


def check_purchasable(item: Item, *, owned: bool) -> int:
    """Цена покупки, если предмет можно купить.

    Raises:
        ItemNotFoundError: предмет неактивен (на витрине его нет).
        AlreadyOwnedError: уже в инвентаре.
        ItemNotAvailableError: у предмета нет цены (открывается уровнем).
    """
    if not item.active:
        raise ItemNotFoundError
    if owned:
        raise AlreadyOwnedError
    if item.price is None:
        raise ItemNotAvailableError
    return item.price


def check_funds(price: int, balance: int) -> None:
    """Баланс покрывает цену.

    Raises:
        InsufficientFundsError: Версов меньше цены.
    """
    if balance < price:
        raise InsufficientFundsError


def purchase_op_id(user_id: UUID, item_id: UUID) -> str:
    """Идентификатор операции покупки: повтор той же пары - та же проводка (ADR 0045)."""
    return f"shop:{user_id}:{item_id}"


# ------------------------------------------------------------------ админка предметов
def validate_item_fields(
    *,
    slot: str,
    rarity: str,
    level_required: int | None,
    price: int | None,
    shop_cfg: ShopConfig | None,
) -> None:
    """Слот и редкость описаны в действующем конфиге, условие открытия задано.

    Raises:
        ItemSlotUnknownError: слота нет в конфиге (или секции ``shop`` нет).
        ItemRarityUnknownError: редкости нет в конфиге.
        ItemUnlockRequiredError: нет ни ``level_required``, ни ``price``.
    """
    if shop_cfg is None or shop_cfg.slot(slot) is None:
        raise ItemSlotUnknownError
    if shop_cfg.rarity(rarity) is None:
        raise ItemRarityUnknownError
    if level_required is None and price is None:
        raise ItemUnlockRequiredError


def check_activation(*, active: bool, asset_key: str | None) -> None:
    """Активный предмет обязан иметь картинку."""
    if active and asset_key is None:
        raise ItemMediaMissingError


def check_key_unchanged(current: str, requested: str) -> None:
    """Ключ предмета неизменяем."""
    if current != requested:
        raise ItemKeyImmutableError


def validate_custom_avatar(
    shop_cfg: ShopConfig,
    slot_by_key: Mapping[str, str],
    sellable: Collection[str] | None = None,
) -> None:
    """Товар «своя аватарка» (если задан) существует, продаётся и лежит в слоте ``shop.slots``.

    Args:
        shop_cfg: Секция ``shop`` конфига.
        slot_by_key: Слоты имеющихся предметов по ключу.
        sellable: Ключи предметов с ценой (продаются за Версы); ``None`` - не проверять.
            Без цены право нельзя получить, а заявки на аватарку невозможны.

    Raises:
        CustomAvatarItemError: предмета нет, у него нет цены или его слот неизвестен.
    """
    key = shop_cfg.custom_avatar_item_key
    if key is None:
        return
    slot = slot_by_key.get(key)
    if slot is None:
        raise CustomAvatarItemError(f"shop.custom_avatar_item_key: нет предмета {key!r}")
    if shop_cfg.slot(slot) is None:
        raise CustomAvatarItemError(f"shop.custom_avatar_item_key: слот {slot!r} не в shop.slots")
    if sellable is not None and key not in sellable:
        raise CustomAvatarItemError(f"shop.custom_avatar_item_key: предмет {key!r} без цены")


# ------------------------------------------------------------------ медиа предметов
def max_upload_bytes(kind: MediaKind, limits: MediaLimits) -> int:
    """Наибольший допустимый размер файла для вида предмета (читать больше не нужно)."""
    return limits.animated_max_bytes if kind is MediaKind.ANIMATED else limits.static_max_bytes


def check_media(info: MediaInfo, kind: MediaKind, limits: MediaLimits) -> None:
    """Файл подходит предмету: формат, размер, кадры, длительность, сторона (ADR 0046).

    Raises:
        ItemMediaTooLargeError: размер файла больше лимита вида.
        ItemMediaInvalidError: формат, число кадров, длительность или сторона не подходят.
    """
    if info.size_bytes > max_upload_bytes(kind, limits):
        raise ItemMediaTooLargeError
    if kind is MediaKind.ANIMATED:
        if info.format not in _ANIMATED_FORMATS:
            raise ItemMediaInvalidError("анимация - только animated WebP")
        if info.frames < _ANIMATED_MIN_FRAMES:
            raise ItemMediaInvalidError("в файле нет анимации")
        if info.frames > limits.animated_max_frames:
            raise ItemMediaInvalidError("слишком много кадров")
        if info.duration_ms > limits.animated_max_duration_ms:
            raise ItemMediaInvalidError("анимация слишком длинная")
    else:
        if info.format not in _STATIC_FORMATS:
            raise ItemMediaInvalidError("статика - только WebP или PNG")
        if info.frames != 1:
            raise ItemMediaInvalidError("статичная картинка не должна быть анимированной")
    if max(info.width, info.height) > limits.max_side_px:
        raise ItemMediaInvalidError("сторона картинки больше допустимой")


def object_key(token_hex: str, extension: str) -> str:
    """Ключ объекта ``c/<32 hex>.<ext>``: случайный, перечислить его нельзя.

    Raises:
        ValueError: ``token_hex`` - не 32 hex-символа.
    """
    if not _TOKEN.match(token_hex):
        raise ValueError("ключ объекта строится из 32 hex-символов")
    return f"{OBJECT_PREFIX}{token_hex}.{extension}"
