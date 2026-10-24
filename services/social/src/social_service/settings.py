"""Настройки social (переменные окружения ``GV_*``).

Только эксплуатационные параметры процесса. Лимиты, окна, справочник причин и пределы санкций
живут в версионируемом конфиге в БД (ADR 0053).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from gv_service import ServiceSettings


class SocialSettings(BaseModel):
    """Эксплуатационные настройки: ``GV_SOCIAL__*``."""

    model_config = ConfigDict(frozen=True)

    # Кэш действующего конфига в процессе: публикация действует через столько секунд.
    config_cache_ttl_s: float = Field(default=5.0, ge=0, le=300)
    # Каталог со стартовым конфигом (`config.yaml`): публикуется как версия 1 только в local/ci
    # и только пока нет опубликованной версии.
    bootstrap_config_dir: Path | None = None
    bootstrap_interval_s: float = Field(default=60.0, gt=0)
    # Как часто задача relay снимает истёкшие санкции (ADR 0052).
    sanction_expiry_interval_s: float = Field(default=30.0, gt=0)
    # Подсказка клиенту, когда лимитер (Redis) недоступен и операция закрыта (503).
    limiter_retry_after_s: int = Field(default=5, ge=1, le=300)
    # Heartbeat присутствия: не больше стольких вызовов за окно `presence.heartbeat_s` конфига.
    presence_burst: int = Field(default=3, ge=1, le=100)


class Settings(ServiceSettings):
    """Настройки сервиса. Новые поля добавлять здесь с безопасными значениями по умолчанию."""

    service_name: str = "social"
    social: SocialSettings = SocialSettings()
