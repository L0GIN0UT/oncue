"""Схемы internal API game для match (план этапа 4, D5, §2.5; ADR 0033).

План раундов уходит только match по сервисному токену и хранится в снимке комнаты; клиенту
он не передаётся (античит §12 п. 1). Поэтому здесь, в отличие от публичных схем, есть
``media_item_id``, названия, алиасы и ``asset_set_id``.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from game_service.api.public.schemas import Domain, ModeKey, OptionId

MAX_PLAN_ROUNDS = 50
"""Потолок раундов матча (``OnlineSessionSpec.rounds`` ≤ 50)."""
MAX_PLAN_COLLECTIONS = 10
"""Потолок сборников в схеме; рабочий предел — ``source.collection.max_collections``."""
MAX_EXCLUDE_IDS = 5000
"""Память сыгранного лобби (``exclude_ids``), D12."""
Seed = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.:-]{8,128}$")]


class RoundPlanRequest(BaseModel):
    """Запрос плана матча (D5): детерминирован по ``seed`` при том же пуле."""

    model_config = ConfigDict(extra="forbid")

    mode_key: ModeKey
    version: int | None = Field(
        default=None, ge=1, description="Версия шаблона; null — текущая опубликованная"
    )
    seed: Seed = Field(
        description=(
            "Сид розыгрыша: секретный, случайный на каждый старт матча "
            "(match хранит его только в снимке комнаты; ADR 0033)"
        )
    )
    rounds: int = Field(ge=1, le=MAX_PLAN_ROUNDS)
    collection_ids: list[UUID] = Field(default_factory=list, max_length=MAX_PLAN_COLLECTIONS)
    exclude_ids: list[UUID] = Field(
        default_factory=list,
        max_length=MAX_EXCLUDE_IDS,
        description="Единицы, которые не должны выпасть (память лобби)",
    )


class ModeConfigBody(BaseModel):
    """Опубликованная версия конфига режима: тело — по ``contracts/modes/mode_config.v1.json``."""

    key: str
    version: int = Field(ge=1)
    domain: Domain
    body: dict[str, Any] = Field(description="Тело конфига (`ModeConfig`), включая `session`")


class PersonKey(BaseModel):
    """Персона единицы (для partial-ключей matcher-а: исполнитель, режиссёр)."""

    person_id: UUID
    name: str
    normalized: str = Field(description="Нормализованное имя (`gv_text`)")
    role: str


class AnswerKey(BaseModel):
    """Ключ ответа раунда: всё, что нужно ядру для `answer.check` без вызова game.

    Повторяет ``gv_game_kernel.MediaItemRef`` (алиасы — уже нормализованные).
    """

    media_item_id: UUID
    domain: Domain
    kind: str
    title: str
    year: int | None
    aliases: list[str] = Field(description="Нормализованные алиасы всех видов")
    title_aliases: list[str] = Field(description="Алиасы видов `title`/`alt` (подмножество)")
    persons: list[PersonKey]
    partial: list[str] = Field(
        description="Ключи partial-matcher-ов режима (`answer.partial`), по которым есть данные"
    )


class LevelAsset(BaseModel):
    """Набор ассетов уровня: ссылку match запрашивает у media-pipeline при открытии (D11)."""

    level: int = Field(ge=1, le=20)
    asset_set_id: UUID


class PlanOption(BaseModel):
    """Вариант ответа ``pick``: ``option_id`` непрозрачен, за ним — единица (только для match)."""

    option_id: OptionId
    label_key: str | None = None
    media_item_id: UUID | None = None
    title: str | None = None


class PlannedRound(BaseModel):
    """Раунд плана: загаданная единица, ключ ответа, ассеты по уровням."""

    index: int = Field(ge=0, lt=MAX_PLAN_ROUNDS)
    answer: AnswerKey
    levels: list[LevelAsset] = Field(min_length=1, max_length=20)
    reveal_asset_set_id: UUID | None = Field(
        default=None, description="Набор ассетов ревила (`clue.reveal`), если задан"
    )
    options: list[PlanOption] = Field(default_factory=list, description="Только формат `pick`")


class PlanCollection(BaseModel):
    """Сборник плана: версия состава фиксируется (ADR 0028)."""

    id: UUID
    version: int = Field(ge=1, description="content_version сборника")


class RoundPlan(BaseModel):
    """План матча: версия и тело конфига плюс разыгранные раунды."""

    mode: ModeConfigBody
    seed: Seed
    rounds: list[PlannedRound] = Field(min_length=1, max_length=MAX_PLAN_ROUNDS)
    collections: list[PlanCollection] = Field(default_factory=list)
