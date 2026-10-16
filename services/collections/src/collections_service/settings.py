"""Настройки collections (переменные окружения ``GV_*``, план этапа 3b §2.8)."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gv_service import ServiceSettings, UgcSettings


class CollectionsLimits(BaseModel):
    """Лимиты UGC (D8, ADR 0030): ``GV_COLLECTIONS__LIMITS__*``.

    Attributes:
        drafts_max: Черновиков (неопубликованных сборников) у автора одновременно.
        publish_per_day: Публикаций у автора за сутки (fail-closed).
        items_min: Минимум единиц для публикации.
        items_max: Максимум единиц в сборнике (не больше схемы события — 200).
        tags_max: Тегов у сборника.
        title_max: Длина названия после нормализации (кодовые точки).
        description_max: Длина описания после нормализации.
        emoji_max_graphemes: Графем в своих эмодзи единицы.
        reactions_per_min: Лайки и избранное в минуту на пользователя (fail-open).
        search_per_min: Запросы каталога с ``q`` в минуту на субъекта (fail-open).
    """

    model_config = ConfigDict(frozen=True)

    drafts_max: int = Field(default=20, ge=1, le=1000)
    publish_per_day: int = Field(default=5, ge=1, le=1000)
    items_min: int = Field(default=5, ge=1, le=200)
    items_max: int = Field(default=200, ge=1, le=200)
    tags_max: int = Field(default=5, ge=0, le=20)
    title_max: int = Field(default=80, ge=1, le=200)
    description_max: int = Field(default=500, ge=0, le=5000)
    emoji_max_graphemes: int = Field(default=12, ge=1, le=12)
    reactions_per_min: int = Field(default=60, ge=1)
    search_per_min: int = Field(default=120, ge=1)

    @model_validator(mode="after")
    def _items_range(self) -> Self:
        if self.items_min > self.items_max:
            raise ValueError("items_min не может быть больше items_max")
        return self


class DifficultySettings(BaseModel):
    """Сложность сборника (D9): ``GV_COLLECTIONS__DIFFICULTY__*``.

    ``difficulty = 1 − (prior·m + Σ ratio) / (m + n)``, где ``ratio = score / max_score``
    первого завершения игрока на версию сборника; пока ``n < min_plays`` — ``null``.

    Attributes:
        prior: Априорная доля очков (0..1).
        m: Вес априорной оценки (число «виртуальных» завершений).
        min_plays: Сколько завершений нужно, чтобы показать сложность.
        thresholds: Границы корзин ``easy < t0 ≤ medium < t1 ≤ hard`` (JSON-массив в env).
    """

    model_config = ConfigDict(frozen=True)

    prior: float = Field(default=0.5, ge=0, le=1)
    m: int = Field(default=10, ge=0, le=10000)
    min_plays: int = Field(default=10, ge=1, le=10000)
    thresholds: tuple[float, float] = (0.4, 0.7)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        low, high = self.thresholds
        if not 0 < low < high < 1:
            raise ValueError("thresholds: нужно 0 < easy/medium < medium/hard < 1")
        return self


class CollectionsSettings(BaseModel):
    """Настройки домена сборников: ``GV_COLLECTIONS__*``."""

    model_config = ConfigDict(frozen=True)

    limits: CollectionsLimits = CollectionsLimits()
    difficulty: DifficultySettings = DifficultySettings()


class Settings(ServiceSettings):
    """Настройки сервиса. Новые поля добавлять здесь с безопасными значениями по умолчанию."""

    service_name: str = "collections"
    collections: CollectionsSettings = CollectionsSettings()
    ugc: UgcSettings = UgcSettings()
