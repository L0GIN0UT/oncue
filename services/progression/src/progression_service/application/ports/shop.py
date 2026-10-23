"""Порты потока S1: предметы, инвентарь, оформление, промокоды, паки, лимитер, хешер, хранилище.

Реализации - в ``infrastructure`` (Postgres, Redis, S3, Pillow). Application видит только эти
протоколы (ADR 0045, 0046, 0048).
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from progression_service.domain.config import MediaLimits
from progression_service.domain.inventory import InventoryItem
from progression_service.domain.packs import Pack
from progression_service.domain.promo import PromoCode, RewardKind
from progression_service.domain.shop import Item, MediaInfo, MediaKind


@dataclass(frozen=True, slots=True)
class PromoBatchRow:
    """Партия кодов для списка админки (без самих кодов) и счётчик активаций."""

    id: UUID
    label: str
    reward_kind: RewardKind
    verses_amount: int | None
    item_id: UUID | None
    max_uses: int
    per_user_once: bool
    expires_at: datetime | None
    codes_total: int
    activations: int
    revoked_at: datetime | None
    created_at: datetime


class ItemRepository(Protocol):
    """Предметы косметики."""

    async def get(self, item_id: UUID, *, for_update: bool = False) -> Item | None:
        """Предмет по id; ``for_update`` блокирует строку до конца транзакции."""
        ...

    async def get_by_key(self, key: str) -> Item | None:
        """Предмет по ключу."""
        ...

    async def by_ids(self, item_ids: Collection[UUID]) -> dict[UUID, Item]:
        """Предметы по id (отсутствующие пропускаются)."""
        ...

    async def list_active(self, slot: str | None) -> list[Item]:
        """Активные предметы витрины (старые первыми), по слоту при заданном."""
        ...

    async def add(self, item: Item) -> bool:
        """Создать предмет; ``False`` - ключ занят."""
        ...

    async def save(self, item: Item) -> None:
        """Записать изменяемые поля предмета (ключ не меняется)."""
        ...

    async def page(
        self,
        *,
        slot: str | None,
        active: bool | None,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[Item]:
        """Страница предметов, новые первыми, строго до курсора ``(created_at, id)``."""
        ...

    async def owners(self, item_ids: Collection[UUID]) -> dict[UUID, int]:
        """Сколько игроков владеет предметом."""
        ...


class InventoryRepository(Protocol):
    """Инвентарь игрока."""

    async def get(self, user_id: UUID, item_id: UUID, now: datetime) -> InventoryItem | None:
        """Строка инвентаря, если предмет не просрочен."""
        ...

    async def add(self, entry: InventoryItem) -> bool:
        """Выдать предмет; ``False`` - уже есть (``ON CONFLICT DO NOTHING``)."""
        ...

    async def grant_level_items(self, user_id: UUID, level: int, now: datetime) -> int:
        """Выдать активные предметы с ``level_required <= level`` (``source = level``).

        Без проводки: уровень открывает предмет бесплатно.

        Returns:
            Сколько предметов выдано.
        """
        ...

    async def entries(self, user_id: UUID, now: datetime) -> list[InventoryItem]:
        """Непросроченные предметы игрока, старые первыми."""
        ...

    async def owned_ids(self, user_id: UUID, now: datetime) -> set[UUID]:
        """Идентификаторы непросроченных предметов игрока."""
        ...


class EquippedRepository(Protocol):
    """Оформление: по одному предмету на слот."""

    async def mine(self, user_id: UUID) -> dict[str, UUID]:
        """Слот -> предмет игрока."""
        ...

    async def many(self, user_ids: Collection[UUID]) -> dict[UUID, dict[str, UUID]]:
        """Оформление нескольких игроков."""
        ...

    async def put(self, user_id: UUID, slot: str, item_id: UUID, now: datetime) -> None:
        """Надеть предмет в слот (заменяет прежний)."""
        ...

    async def clear(self, user_id: UUID, slot: str) -> None:
        """Освободить слот."""
        ...


class PromoRepository(Protocol):
    """Партии, коды и погашения."""

    async def lock_code(self, code_hash: str) -> PromoCode | None:
        """Код с условиями партии под ``FOR UPDATE`` (строка кода)."""
        ...

    async def redeemed(self, code_hash: str, user_id: UUID) -> bool:
        """Игрок уже погасил этот код."""
        ...

    async def batch_redeemed_by(self, batch_id: UUID, user_id: UUID) -> bool:
        """Игрок уже погасил какой-либо код этой партии."""
        ...

    async def add_redemption(
        self, code_hash: str, user_id: UUID, code_id: UUID, batch_id: UUID, now: datetime
    ) -> None:
        """Записать погашение (PK ``(code_hash, user_id)``) и увеличить счётчик кода."""
        ...

    async def add_batch(
        self,
        row: PromoBatchRow,
        created_by: UUID,
        codes: Sequence[tuple[UUID, str]],
    ) -> None:
        """Партия и её коды ``(code_id, code_hash)`` одной вставкой."""
        ...

    async def page(
        self, *, before: tuple[datetime, UUID] | None, limit: int
    ) -> list[PromoBatchRow]:
        """Партии, новые первыми, строго до курсора."""
        ...

    async def revoke(self, batch_id: UUID, now: datetime) -> bool:
        """Отозвать партию; ``False`` - партии нет."""
        ...


class PackRepository(Protocol):
    """Паки косметики (только модель, 5b)."""

    async def get(self, pack_id: UUID) -> Pack | None:
        """Пак по id."""
        ...

    async def listing(self, *, active_only: bool) -> list[Pack]:
        """Паки (новые первыми)."""
        ...

    async def add(self, pack: Pack) -> bool:
        """Создать пак; ``False`` - ключ занят."""
        ...

    async def replace(self, pack: Pack) -> bool:
        """Заменить поля и состав; ``False`` - ключ занят другим паком."""
        ...


class CodeHasher(Protocol):
    """Хеш промокода: HMAC-SHA256 серверным ключом (ADR 0048)."""

    def hash(self, normalized_code: str) -> str:
        """Hex-хеш нормализованного кода."""
        ...

    def same(self, left: str, right: str) -> bool:
        """Сравнение хешей за постоянное время."""
        ...


@dataclass(frozen=True, slots=True)
class AttemptWindow:
    """Попытки в текущем окне: сколько уже было и через сколько окно закончится."""

    count: int
    resets_in_s: int


class AttemptLimiter(Protocol):
    """Счётчик попыток ввода кода (Redis, окно); отказ хранилища - fail-closed."""

    async def hit(self, key: str, window_s: int) -> AttemptWindow:
        """Учесть попытку и вернуть состояние окна.

        Raises:
            DependencyUnavailableError: хранилище недоступно (попытка отклоняется).
        """
        ...


class MediaStore(Protocol):
    """Хранилище картинок предметов (S3, публичный префикс ``c/``)."""

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        """Записать объект."""
        ...

    async def delete(self, keys: Sequence[str]) -> None:
        """Удалить объекты (отсутствующие пропускаются)."""
        ...

    def public_url(self, key: str) -> str:
        """Публичный адрес объекта."""
        ...


@dataclass(frozen=True, slots=True)
class InspectedMedia:
    """Результат разбора файла: сведения, расширение, MIME и постер (первый кадр)."""

    info: MediaInfo
    extension: str
    content_type: str
    poster: bytes | None


class MediaInspector(Protocol):
    """Проверка файла: сигнатура, декодирование всех кадров, постер."""

    def inspect(self, data: bytes, kind: MediaKind, limits: MediaLimits) -> InspectedMedia:
        """Разобрать файл.

        Raises:
            ItemMediaInvalidError: не картинка, подмена формата, ошибка декодирования.
            ItemMediaTooLargeError: размер или число пикселей выше лимитов.
        """
        ...


class TokenSource(Protocol):
    """Случайные токены для ключей объектов."""

    def token_hex(self) -> str:
        """32 hex-символа (128 бит)."""
        ...
