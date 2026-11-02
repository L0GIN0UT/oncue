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
    # 6, а не 3: штатный тик + повтор после 401 + возврат на вкладку не должны давать 429.
    presence_burst: int = Field(default=6, ge=1, le=100)
    # Публичный счётчик онлайна: копия числа в процессе (с) и лимит чтений с одного IP в минуту.
    online_count_cache_s: float = Field(default=5.0, ge=0, le=300)
    online_count_per_minute: int = Field(default=120, ge=1, le=100_000)
    # Пакетные `players/relations`: вызовов на пользователя за `RELATIONS_WINDOW_S` (60 с).
    relations_per_minute: int = Field(default=60, ge=1, le=10_000)


class Settings(ServiceSettings):
    """Настройки сервиса. Новые поля добавлять здесь с безопасными значениями по умолчанию."""

    service_name: str = "social"
    social: SocialSettings = SocialSettings()
