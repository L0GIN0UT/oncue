"""Порты application-слоя: хранилище, исходники, обработка медиа, подпись ссылок, репозитории.

Реализации — в ``infrastructure``; фейки для unit-тестов — в ``tests/conftest.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from gv_core.uow import UnitOfWork
from gv_db.idempotency import IdempotencyStore
from media_pipeline_service.domain.asset_set import AssetSet
from media_pipeline_service.domain.clues import MaskRegion
from media_pipeline_service.domain.media_items import MediaItemState
from media_pipeline_service.domain.sources import SourceMedia


# ---------------------------------------------------------------- хранилище
class StorageArea(StrEnum):
    """Логический бакет: исходники (только для сервиса) и ассеты (раздаёт gateway)."""

    SOURCES = "sources"
    ASSETS = "assets"


class ObjectStorage(Protocol):
    """Приватное S3-совместимое хранилище."""

    async def put(self, area: StorageArea, key: str, data: bytes, content_type: str) -> None:
        """Записать объект (перезапись допустима)."""
        ...

    async def get(self, area: StorageArea, key: str, *, max_bytes: int) -> bytes:
        """Прочитать объект не больше ``max_bytes`` байт, иначе ``GenerationError``."""
        ...

    async def delete(self, area: StorageArea, keys: Sequence[str]) -> None:
        """Удалить объекты (отсутствующие — не ошибка)."""
        ...


# ---------------------------------------------------------------- исходники и обработка
@dataclass(frozen=True, slots=True)
class FetchedSource:
    """Скачанный исходник."""

    data: bytes
    content_type: str | None


class SourceFetcher(Protocol):
    """Загрузка исходника по адресу из каталога (с защитой от SSRF)."""

    async def fetch(self, uri: str) -> FetchedSource:
        """Скачать. Ошибки — ``GenerationError`` с кодом ``source.*``."""
        ...


@dataclass(frozen=True, slots=True)
class NormalizedMedia:
    """Перекодированный исходник без метаданных."""

    data: bytes
    mime: str
    ext: str


class SourceNormalizer(Protocol):
    """Нормализация исходника одного семейства (аудио или изображение)."""

    async def normalize(self, data: bytes) -> NormalizedMedia:
        """Перекодировать: проверка, что файл декодируется, и удаление метаданных."""
        ...


class Transcoder(SourceNormalizer, Protocol):
    """Аудио: нормализация, длительность, нарезка отрывков."""

    async def probe_duration_ms(self, data: bytes) -> int:
        """Длительность аудио, мс."""
        ...

    async def cut_audio(self, data: bytes, *, start_s: float, duration_s: float) -> bytes:
        """Отрывок ``[start_s, start_s + duration_s)`` — AAC в fMP4 без метаданных.

        Декодер читает только этот интервал: результат физически не содержит более
        позднего аудио.
        """
        ...


@dataclass(frozen=True, slots=True)
class ImageOps:
    """Преобразование изображения для одного уровня.

    Attributes:
        max_width: Ширина результата не больше этой (без увеличения).
        crop: Доля центральной части кадра по каждой оси (1 — весь кадр).
        blur_px: Радиус гауссова размытия в пикселях результата.
        pixel_block: Размер блока пикселизации (1 — без пикселизации).
        mask_regions: Закрашиваемые области (доли сторон исходного кадра).
    """

    max_width: int
    crop: float = 1.0
    blur_px: float = 0.0
    pixel_block: int = 1
    mask_regions: tuple[MaskRegion, ...] = ()


@dataclass(frozen=True, slots=True)
class RenderedImage:
    """Готовое изображение уровня (WebP без EXIF)."""

    data: bytes
    width: int
    height: int
    mime: str = "image/webp"
    ext: str = "webp"


class ImageProcessor(SourceNormalizer, Protocol):
    """Изображения: нормализация и рендер уровней."""

    async def render(self, data: bytes, ops: ImageOps) -> RenderedImage:
        """Применить преобразования уровня."""
        ...


# ---------------------------------------------------------------- ссылки
class UrlSigner(Protocol):
    """Подпись ссылки на объект ассета (ADR 0012: nginx ``secure_link``)."""

    def sign(self, object_key: str, expires_at: datetime) -> str:
        """URL объекта, действительный до ``expires_at``."""
        ...


class LinkRateLimiter(Protocol):
    """Счётчик выдач ссылок в скользящем окне."""

    async def hit(self, key: str, window_s: int) -> int:
        """Учесть выдачу и вернуть число выдач в текущем окне (включая эту)."""
        ...


class TokenSource(Protocol):
    """Источник случайных токенов для ключей объектов."""

    def token_hex(self) -> str:
        """32 hex-символа (128 бит криптографической случайности)."""
        ...


# ---------------------------------------------------------------- репозитории
class AssetSetRepository(Protocol):
    """Наборы ассетов."""

    async def get(self, asset_set_id: UUID, *, for_update: bool = False) -> AssetSet | None:
        """Набор по id."""
        ...

    async def list_for_item(
        self, media_item_id: UUID, clue_type: str | None = None, *, for_update: bool = False
    ) -> list[AssetSet]:
        """Наборы единицы (опционально одного типа загадки), по времени создания."""
        ...

    async def add(self, aggregate: AssetSet) -> None:
        """Сохранить новый набор."""
        ...

    async def save(self, aggregate: AssetSet) -> None:
        """Сохранить изменения с оптимистичной блокировкой (``ConflictError`` при гонке)."""
        ...


class SourceMediaRepository(Protocol):
    """Исходники."""

    async def get(self, source_id: UUID) -> SourceMedia | None:
        """Исходник по id."""
        ...

    async def list_for_item(self, media_item_id: UUID) -> list[SourceMedia]:
        """Исходники единицы."""
        ...

    async def add(self, source: SourceMedia) -> None:
        """Сохранить новый исходник."""
        ...

    async def save(self, source: SourceMedia) -> None:
        """Сохранить изменения исходника."""
        ...


class MediaItemStateRepository(Protocol):
    """Проекция единиц каталога."""

    async def get(self, media_item_id: UUID, *, for_update: bool = False) -> MediaItemState | None:
        """Проекция по id."""
        ...

    async def upsert(self, state: MediaItemState) -> None:
        """Создать или обновить проекцию."""
        ...

    async def next_event_version(self, media_item_id: UUID) -> int:
        """Следующая версия событий ``AssetsReady``/``AssetsFailed`` единицы.

        Счётчик растёт в транзакции вызывающего (той же, где событие пишется в outbox) и
        блокирует строку единицы до коммита — версии монотонны по ``media_item_id``
        (возможны пропуски, если транзакция откатилась или событие не понадобилось).

        Raises:
            MediaItemNotFoundError: проекции единицы нет.
        """
        ...


class LinkIssuanceJournal(Protocol):
    """Журнал выдачи ссылок (ADR 0012)."""

    async def record(
        self,
        *,
        asset_set_id: UUID,
        level: int,
        subject_hash: str,
        issued_at: datetime,
        expires_at: datetime,
        client_id: str | None = None,
    ) -> None:
        """Записать выдачу. Субъект — только sha256-хэшем (ПДн не храним).

        ``client_id`` — вызывающий сервис из сервисного токена (D13 плана этапа 2).
        """
        ...

    async def purge_older_than(self, cutoff: datetime, *, limit: int) -> int:
        """Удалить записи старше ``cutoff`` (не больше ``limit``), вернуть число удалённых."""
        ...


@dataclass(frozen=True, slots=True)
class AdminAction:
    """Запись аудита admin-действия (§8 п.7)."""

    actor_id: UUID
    action: str
    target_id: UUID
    reason: str
    at: datetime
    details: dict[str, Any] = field(default_factory=dict)


class AdminAuditLog(Protocol):
    """Аудит admin-действий в сервисе (append-only)."""

    async def record(self, action: AdminAction) -> None:
        """Записать действие."""
        ...


class MediaUnitOfWork(UnitOfWork, Protocol):
    """Транзакция use-case с репозиториями сервиса."""

    asset_sets: AssetSetRepository
    sources: SourceMediaRepository
    items: MediaItemStateRepository
    links: LinkIssuanceJournal
    # Ключи admin-мутаций в транзакции этого UoW (``gv_db.idempotency``).
    idempotency: IdempotencyStore
    audit: AdminAuditLog


UnitOfWorkFactory = Callable[[], MediaUnitOfWork]
