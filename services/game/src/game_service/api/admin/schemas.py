"""Схемы admin-API game (план этапа 2, §2.6) — контракт для админки (поток E).

Мутации: ``Idempotency-Key`` + ``reason`` (ADR 0015). Тело конфига — произвольный JSON:
его проверяет сервер (схема ``contracts/modes`` + реестр плагинов), ошибки — с путями.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from game_service.api.public.schemas import Domain
from gv_service import Reason

ConfigStatus = Literal["draft", "published", "archived"]
DailySetStatus = Literal["scheduled", "locked", "failed"]

# Any: тело конфига — JSON-объект автора; проверяется сервером, а не моделью запроса.
type ConfigBody = dict[str, Any]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ----------------------------------------------------------------------------- валидация
class ValidationErrorItem(BaseModel):
    """Ошибка конфига: путь поля, машинный код и ключ i18n текста."""

    path: str
    code: str
    message_key: str


class ValidationWarning(BaseModel):
    """Предупреждение (например, пул меньше рекомендуемого)."""

    code: str


class PoolInfo(BaseModel):
    """Размер пула по проекции game."""

    playable_count: int = Field(ge=0)


class ValidationResult(BaseModel):
    """Итог проверки конфига."""

    ok: bool
    errors: list[ValidationErrorItem]
    warnings: list[ValidationWarning]
    pool: PoolInfo | None = Field(description="null, если тело не разобрано")


class ValidateRequest(_Request):
    """Проверить тело конфига без сохранения."""

    body: ConfigBody


# ----------------------------------------------------------------------------- конфиги
class ModeConfigSummary(BaseModel):
    """Строка списка режимов."""

    key: str
    domain: Domain
    status: ConfigStatus = Field(description="draft — режим ещё ни разу не опубликован")
    published_version: int | None
    has_draft: bool
    updated_at: datetime


class ModeConfigVersionSummary(BaseModel):
    """Опубликованная версия (неизменяемая)."""

    version: int
    status: Literal["published", "archived"]
    body_hash: str
    actor_id: UUID | None
    reason: str | None
    created_at: datetime
    rolled_back_from: int | None = None


class DraftOut(BaseModel):
    """Черновик (один на ключ); сохраняется и невалидным."""

    body: ConfigBody
    revision: int
    validation: ValidationResult
    updated_by: UUID | None
    updated_at: datetime


class ModeConfigDetail(BaseModel):
    """Режим: версии и черновик."""

    key: str
    domain: Domain
    status: ConfigStatus
    published_version: int | None
    versions: list[ModeConfigVersionSummary]
    draft: DraftOut | None


class ModeConfigVersion(ModeConfigVersionSummary):
    """Версия с телом."""

    key: str
    body: ConfigBody


class SaveDraftRequest(_Request):
    """Сохранить черновик (оптимистичная блокировка по ``base_revision``)."""

    body: ConfigBody
    base_revision: int | None = Field(
        ge=0, description="Ревизия, от которой правили; null — черновика ещё нет"
    )
    reason: Reason


class PublishRequest(_Request):
    """Опубликовать черновик ревизии ``draft_revision`` (только валидный)."""

    draft_revision: int = Field(ge=0)
    reason: Reason


class RollbackRequest(_Request):
    """Откат: копия версии ``to_version`` публикуется как новая версия."""

    to_version: int = Field(ge=1)
    reason: Reason


class ArchiveRequest(_Request):
    """Убрать режим из каталога (сессии доигрываются)."""

    reason: Reason


class AssetRequestCreate(_Request):
    """Запросить генерацию ассетов под параметры загадки черновика (ADR 0025)."""

    draft_revision: int = Field(ge=1)
    reason: Reason


class AssetRequestOut(BaseModel):
    """Запрос ассетов: статус профиля смотрится в media-pipeline (``/admin/v1/profiles``)."""

    request_id: UUID
    mode_key: str
    draft_revision: int
    clue_type: str
    params_hash: str
    status: Literal["pending", "sent"]
    actor_id: UUID | None
    reason: str
    requested_at: datetime


# ----------------------------------------------------------------------------- наборы дня
class DailySetItem(BaseModel):
    """Единица набора дня."""

    index: int = Field(ge=0)
    media_item_id: UUID
    title: str
    year: int | None
    playable: bool


class DailySet(BaseModel):
    """Набор дня режима."""

    mode_key: str
    day: date
    mode_version: int
    status: DailySetStatus
    items: list[DailySetItem]


class ReplaceItemRequest(_Request):
    """Заменить единицу набора (только ``scheduled``)."""

    media_item_id: UUID
    reason: Reason


class RegenerateRequest(_Request):
    """Перегенерировать набор (только ``scheduled`` / ``failed``)."""

    reason: Reason


class ProjectionItem(BaseModel):
    """Кандидат на замену из проекции game."""

    media_item_id: UUID
    domain: Domain
    kind: str
    title: str
    year: int | None
    status: Literal["active", "blocked"]
    playable: bool | None = Field(
        description="Есть ли ассеты под режим playable_for_mode; null — режим не задан"
    )
