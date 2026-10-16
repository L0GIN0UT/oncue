"""Настройки collections (переменные окружения ``GV_*``, план этапа 3b §2.8)."""

from __future__ import annotations

from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
        emoji_min_graphemes: Минимум графем своих эмодзи: встроенный профиль ``emoji``
            media-pipeline открывает до 6 эмодзи и при меньшем числе набор не строит
            (ADR 0029) — короткие эмодзи отклоняются при публикации.
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
    emoji_min_graphemes: int = Field(default=6, ge=1, le=12)
    reactions_per_min: int = Field(default=60, ge=1)
    search_per_min: int = Field(default=120, ge=1)

    @model_validator(mode="after")
    def _items_range(self) -> Self:
        if self.items_min > self.items_max:
            raise ValueError("items_min не может быть больше items_max")
        if self.emoji_min_graphemes > self.emoji_max_graphemes:
            raise ValueError("emoji_min_graphemes не может быть больше emoji_max_graphemes")
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


class PopularSettings(BaseModel):
    """Сортировка «популярные» (D10): ``GV_COLLECTIONS__POPULAR__*``.

    ``popular_score = like_weight·лайки + play_weight·завершения`` за последние
    ``window_days`` дней; пересчитывает периодическая задача ``collections.popular.recompute``
    порциями по ``batch`` сборников.
    """

    model_config = ConfigDict(frozen=True)

    window_days: int = Field(default=7, ge=1, le=90)
    like_weight: float = Field(default=1.0, ge=0)
    play_weight: float = Field(default=1.0, ge=0)
    interval_s: float = Field(default=600, gt=0)
    jitter_s: float = Field(default=60, ge=0)
    batch: int = Field(default=500, ge=1, le=10000)
    stats_retention_days: int = Field(default=30, ge=1, le=3650)


class SweepSettings(BaseModel):
    """Удаление заблокированных единиц из сборников (K7): ``GV_COLLECTIONS__SWEEP__*``.

    Consumer ``MediaItemBlocked`` сразу обрабатывает первые ``batch`` сборников, остальное —
    задача ``collections.blocked.sweep`` (по одной новой версии на сборник).
    """

    model_config = ConfigDict(frozen=True)

    batch: int = Field(default=100, ge=1, le=10000)
    interval_s: float = Field(default=60, gt=0)
    jitter_s: float = Field(default=10, ge=0)


class CollectionsSettings(BaseModel):
    """Настройки домена сборников: ``GV_COLLECTIONS__*``.

    Attributes:
        meta_config: YAML словарей конструктора — теги и пресеты обложек
            (``GV_COLLECTIONS__META_CONFIG``); пусто — встроенный ``config/meta.yaml``.
    """

    model_config = ConfigDict(frozen=True)

    limits: CollectionsLimits = CollectionsLimits()
    difficulty: DifficultySettings = DifficultySettings()
    popular: PopularSettings = PopularSettings()
    sweep: SweepSettings = SweepSettings()
    meta_config: Path | None = None

    @field_validator("meta_config", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        return None if value == "" else value


class Settings(ServiceSettings):
    """Настройки сервиса. Новые поля добавлять здесь с безопасными значениями по умолчанию."""

    service_name: str = "collections"
    collections: CollectionsSettings = CollectionsSettings()
    ugc: UgcSettings = UgcSettings()
