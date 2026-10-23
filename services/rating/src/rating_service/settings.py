"""Настройки rating (переменные окружения ``GV_*``).

Здесь — только эксплуатационные параметры процесса (интервалы задач, размеры пачек). Всё, что
влияет на игру (Эло, звания, окна подбора, длина сезона, топ недели), живёт в конфиге ladder-а.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from gv_service import ServiceSettings


class RatingSettings(BaseModel):
    """Эксплуатационные настройки: ``GV_RATING__*``."""

    model_config = ConfigDict(frozen=True)

    seasons_interval_s: float = Field(default=30.0, gt=0)
    seasons_jitter_s: float = Field(default=5.0, ge=0)
    weekly_interval_s: float = Field(default=300.0, gt=0)
    weekly_jitter_s: float = Field(default=30.0, ge=0)
    rebuild_interval_s: float = Field(default=10.0, gt=0)
    rebuild_jitter_s: float = Field(default=2.0, ge=0)
    # Каталог сидов ladder-ов: создаются при старте relay только в local/ci.
    bootstrap_ladders_dir: Path | None = None
    bootstrap_interval_s: float = Field(default=3600.0, gt=0)
    # Раз в столько секунд лидерборд сезона пересобирается из Postgres сам: так лечатся
    # сбой записи в Redis и его потеря. Метка «готов» живёт ровно этот срок.
    leaderboard_resync_s: int = Field(default=3600, ge=60)
    # Сколько игроков копировать в новый сезон / читать при пересборке за один запрос.
    batch_size: int = Field(default=1000, ge=10, le=10000)
    # Допуск часов продюсера: `MatchFinished.finished_at` позже «сейчас + допуск» отвергается.
    finished_at_skew_s: int = Field(default=300, ge=0)
    # Окно дат `starts_at` при создании сезона админом: назад на допуск, вперёд на N дней.
    season_start_skew_s: int = Field(default=300, ge=0)
    season_start_max_ahead_days: int = Field(default=366, ge=1)
    # Предел смещения курсора лидерборда (защита от произвольных `ZREVRANGE`).
    leaderboard_max_offset: int = Field(default=100000, ge=0)
    # Как часто публичное чтение может поставить пересборку потерянной доски.
    rebuild_claim_ttl_s: int = Field(default=60, ge=1)


class Settings(ServiceSettings):
    """Настройки сервиса. Новые поля добавлять здесь с безопасными значениями по умолчанию."""

    service_name: str = "rating"
    rating: RatingSettings = RatingSettings()
