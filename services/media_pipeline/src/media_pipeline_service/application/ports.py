"""Порты application-слоя: хранилище, исходники, обработка медиа, подпись ссылок, репозитории.

Реализации — в ``infrastructure``; фейки для unit-тестов — в ``tests/conftest.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, StrEnum
from typing import Any, Literal, Protocol
from uuid import UUID

from gv_core.uow import UnitOfWork
from gv_db.idempotency import IdempotencyStore
from media_pipeline_service.domain.asset_set import AssetSet
from media_pipeline_service.domain.clues import MaskRegion
from media_pipeline_service.domain.collections import CollectionState
from media_pipeline_service.domain.dynamic_profiles import DynamicProfile
from media_pipeline_service.domain.media_items import MediaItemState
from media_pipeline_service.domain.sources import AssetScope, SourceMedia
from media_pipeline_service.domain.ugc import UgcImage, UgcPurpose, UgcStatus


# ---------------------------------------------------------------- хранилище
class StorageArea(StrEnum):
    """Логический бакет: исходники (только для сервиса) и ассеты (раздаёт gateway)."""

    SOURCES = "sources"
    ASSETS = "assets"


@dataclass(frozen=True, slots=True)
class StoredObject:
    """Объект в хранилище: ключ и время последнего изменения."""

    key: str
    last_modified: datetime


@dataclass(frozen=True, slots=True)
class ObjectPage:
    """Страница листинга; ``next_token`` - ``None`` на последней странице."""

    items: list[StoredObject]
    next_token: str | None


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

    async def copy(self, area: StorageArea, source_key: str, target_key: str) -> None:
        """Скопировать объект внутри бакета области (одобрение UGC: ``p/`` → ``u/``)."""
        ...

    async def list_objects(
        self, area: StorageArea, prefix: str, *, limit: int, token: str | None = None
    ) -> ObjectPage:
        """Страница объектов с префиксом (для чистки осиротевших объектов UGC)."""
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

    async def cut_audio(
        self, data: bytes, *, start_s: float, duration_s: float, reverse: bool = False
    ) -> bytes:
        """Отрывок ``[start_s, start_s + duration_s)`` — AAC в fMP4 без метаданных.

        Декодер читает только этот интервал: результат физически не содержит более
        позднего аудио. ``reverse`` — отрывок проигрывается задом наперёд (реверс уже
        ограниченного отрывка).
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


@dataclass(frozen=True, slots=True)
class EncodedImage:
    """Перекодированное UGC-изображение: WebP без метаданных."""

    data: bytes
    width: int
    height: int
    sha256: str


class UgcImageEncoder(Protocol):
    """Проверка и перекодирование пользовательского изображения (безопасность файла)."""

    async def encode(self, data: bytes, purpose: UgcPurpose) -> EncodedImage:
        """Проверить по содержимому и пересобрать из пикселей.

        Raises:
            InvalidUgcImageError: не изображение допустимого формата, анимация, повреждён,
                «бомба» или стороны вне лимитов назначения.
        """
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
class _AnyScope(Enum):
    ANY = "any"


ANY_SCOPE = _AnyScope.ANY
"""Фильтр «наборы любого владельца»: и каталожные, и со ``scope`` (блокировка единицы)."""

ScopeFilter = AssetScope | None | Literal[_AnyScope.ANY]
"""``None`` — только каталожные, ``AssetScope`` — только этого владельца, ``ANY_SCOPE`` — все."""


class AssetSetRepository(Protocol):
    """Наборы ассетов."""

    async def get(self, asset_set_id: UUID, *, for_update: bool = False) -> AssetSet | None:
        """Набор по id."""
        ...

    async def list_for_item(
        self,
        media_item_id: UUID,
        clue_type: str | None = None,
        *,
        for_update: bool = False,
        scope: ScopeFilter = ANY_SCOPE,
    ) -> list[AssetSet]:
        """Наборы единицы (опционально одного типа загадки и владельца), по времени создания."""
        ...

    async def list_for_scope(
        self, scope: AssetScope, *, for_update: bool = False
    ) -> list[AssetSet]:
        """Все наборы владельца (сборника) по единицам."""
        ...

    async def delete(self, asset_set_ids: Sequence[UUID]) -> None:
        """Удалить наборы вместе со строками уровней (объекты S3 удаляет вызывающий)."""
        ...

    async def add(self, aggregate: AssetSet) -> None:
        """Сохранить новый набор."""
        ...

    async def save(self, aggregate: AssetSet) -> None:
        """Сохранить изменения с оптимистичной блокировкой (``ConflictError`` при гонке)."""
        ...

    async def count_by_profile(
        self, clue_type: str | None = None, params_hash: str | None = None
    ) -> list[ProfileSetCount]:
        """Сколько наборов каждого профиля в каком состоянии (для admin-API профилей)."""
        ...


@dataclass(frozen=True, slots=True)
class ProfileSetCount:
    """Число наборов профиля ``(clue_type, profile_key, params_hash)`` в состоянии ``status``."""

    clue_type: str
    profile_key: str
    params_hash: str
    status: str
    count: int


class SourceMediaRepository(Protocol):
    """Исходники."""

    async def get(self, source_id: UUID) -> SourceMedia | None:
        """Исходник по id."""
        ...

    async def list_for_item(
        self, media_item_id: UUID, *, scope: AssetScope | None = None
    ) -> list[SourceMedia]:
        """Исходники единицы одного владельца (``None`` — каталожные), по времени создания."""
        ...

    async def list_for_scope(self, scope: AssetScope) -> list[SourceMedia]:
        """Все исходники владельца (сборника)."""
        ...

    async def scopes_for_item(self, media_item_id: UUID) -> list[AssetScope]:
        """Владельцы, у которых есть свои исходники единицы (досоздание наборов)."""
        ...

    async def add(self, source: SourceMedia) -> None:
        """Сохранить новый исходник."""
        ...

    async def save(self, source: SourceMedia) -> None:
        """Сохранить изменения исходника."""
        ...

    async def delete(self, source_ids: Sequence[UUID]) -> None:
        """Удалить исходники (у наборов пропадает ссылка на них)."""
        ...


class MediaItemStateRepository(Protocol):
    """Проекция единиц каталога."""

    async def get(self, media_item_id: UUID, *, for_update: bool = False) -> MediaItemState | None:
        """Проекция по id."""
        ...

    async def get_many(
        self, media_item_ids: Sequence[UUID], *, for_update: bool = False
    ) -> dict[UUID, MediaItemState]:
        """Проекции нескольких единиц одним запросом; ``for_update`` блокирует строки по
        возрастанию id (порядок блокировок «единица → набор», как у воркера)."""
        ...

    async def upsert(self, state: MediaItemState) -> None:
        """Создать или обновить проекцию."""
        ...

    async def list_active_ids(self, domain: str, *, after: UUID | None, limit: int) -> list[UUID]:
        """id активных единиц домена по возрастанию после ``after`` (досоздание наборов)."""
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


class DynamicProfileRepository(Protocol):
    """Динамические профили (ADR 0025)."""

    async def lock(self) -> None:
        """Сериализовать заведение профилей до конца транзакции (лимит без гонки)."""
        ...

    async def get(
        self, clue_type: str, params_hash: str, domain: str, *, for_update: bool = False
    ) -> DynamicProfile | None:
        """Профиль по ключу дедупликации."""
        ...

    async def count_usable(self) -> int:
        """Сколько профилей не отклонено (лимит ``GV_MEDIA__MAX_DYNAMIC_PROFILES``)."""
        ...

    async def add(self, profile: DynamicProfile) -> None:
        """Сохранить новый профиль."""
        ...

    async def save(self, profile: DynamicProfile) -> None:
        """Сохранить изменения профиля."""
        ...

    async def list_usable(self, domain: str) -> list[DynamicProfile]:
        """Неотклонённые профили домена."""
        ...

    async def list_backfilling(self, limit: int) -> list[DynamicProfile]:
        """Профили, наборы которых ещё досоздаются (старые первыми)."""
        ...

    async def list_all(
        self, clue_type: str | None = None, params_hash: str | None = None
    ) -> list[DynamicProfile]:
        """Все профили (с фильтрами) для admin-API."""
        ...


class CollectionStateRepository(Protocol):
    """Проекция сборников (версия и статус, ADR 0029)."""

    async def advance(self, state: CollectionState) -> bool:
        """Записать состояние, если его версия больше сохранённой (или строки нет).

        Строка остаётся заблокированной до конца транзакции.

        Returns:
            ``False`` — событие устарело или повторное, ничего не изменилось.
        """
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


class UgcImageRepository(Protocol):
    """UGC-изображения."""

    async def add(self, image: UgcImage) -> None:
        """Сохранить новую заявку."""
        ...

    async def get(self, image_id: UUID, *, for_update: bool = False) -> UgcImage | None:
        """Ассет по id (``for_update`` сериализует конкурирующие решения)."""
        ...

    async def save(self, image: UgcImage) -> None:
        """Сохранить изменения решения."""
        ...

    async def list_approved_for_owner(
        self, owner_user_id: UUID, purpose: str, *, exclude: UUID
    ) -> list[UgcImage]:
        """Одобренные изображения владельца (кроме ``exclude``), строки блокируются."""
        ...

    async def pending_keys(self, keys: Sequence[str]) -> set[str]:
        """Какие из ключей принадлежат ``pending``-заявкам."""
        ...

    async def enqueue_purge(self, keys: Sequence[str], now: datetime) -> None:
        """Записать ключи на гарантированное удаление (в транзакции решения)."""
        ...

    async def claim_purge(self, limit: int) -> list[str]:
        """Ключи к удалению (с блокировкой строк: параллельные запуски не пересекаются)."""
        ...

    async def forget_purge(self, keys: Sequence[str]) -> None:
        """Убрать ключи из очереди после успешного удаления из хранилища."""
        ...

    async def lock_owner(self, owner_user_id: UUID, purpose: str) -> None:
        """Сериализовать заявки владельца на одно назначение до конца транзакции."""
        ...

    async def list_pending_for_owner(self, owner_user_id: UUID, purpose: str) -> list[UgcImage]:
        """Ожидающие решения заявки владельца (с блокировкой строк)."""
        ...

    async def queue(
        self,
        status: UgcStatus,
        purpose: str | None,
        *,
        after: tuple[datetime, UUID] | None,
        limit: int,
    ) -> list[UgcImage]:
        """Страница очереди: ``(submitted_at, id)`` по возрастанию, строго после ``after``."""
        ...


class MediaUnitOfWork(UnitOfWork, Protocol):
    """Транзакция use-case с репозиториями сервиса."""

    ugc: UgcImageRepository
    asset_sets: AssetSetRepository
    sources: SourceMediaRepository
    items: MediaItemStateRepository
    links: LinkIssuanceJournal
    profiles: DynamicProfileRepository
    collections: CollectionStateRepository
    # Ключи admin-мутаций в транзакции этого UoW (``gv_db.idempotency``).
    idempotency: IdempotencyStore
    audit: AdminAuditLog


UnitOfWorkFactory = Callable[[], MediaUnitOfWork]
