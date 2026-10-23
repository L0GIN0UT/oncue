"""Настройки progression (переменные окружения ``GV_*``).

Здесь — только эксплуатационные параметры процесса. Всё, что влияет на игру (опыт, уровни,
Версы, потолки, часовой пояс суток), живёт в версионируемом конфиге progression.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from gv_service import ServiceSettings


class ProgressionSettings(BaseModel):
    """Эксплуатационные настройки: ``GV_PROGRESSION__*``."""

    model_config = ConfigDict(frozen=True)

    # Сверка «сумма журнала = баланс/опыт» (ADR 0043): период и разброс запусков задачи.
    reconcile_interval_s: float = Field(default=300.0, gt=0)
    reconcile_jitter_s: float = Field(default=30.0, ge=0)
    # Сколько расхождений читать и логировать за один запуск.
    reconcile_limit: int = Field(default=100, ge=1, le=10000)
    # Потолок времени запроса сверки: тяжёлая агрегация не должна держать БД дольше.
    reconcile_statement_timeout_ms: int = Field(default=30_000, ge=100, le=600_000)
    # Сколько последних проводок отдаёт ``GET /v1/me/wallet``.
    wallet_recent_limit: int = Field(default=20, ge=1, le=100)


class Settings(ServiceSettings):
    """Настройки сервиса. Новые поля добавлять здесь с безопасными значениями по умолчанию."""

    service_name: str = "progression"
    progression: ProgressionSettings = ProgressionSettings()
