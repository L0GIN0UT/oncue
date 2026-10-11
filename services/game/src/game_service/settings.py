"""Настройки game (переменные окружения ``GV_*``)."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from gv_service import ServiceSettings


class GameSettings(BaseModel):
    """Игровые настройки: ``GV_GAME__*`` (план этапа 2, §5 «Настройки»)."""

    model_config = ConfigDict(frozen=True)

    # Адрес media-pipeline для выдачи ссылок на ассеты уровней.
    media_url: str = "http://media-pipeline:8000"
    # aud сервисного токена для вызовов media-pipeline (D13); учётные данные клиента —
    # общие GV_SERVICE_AUTH__* (libs/service, шаг 0b).
    media_audience: str = "media_pipeline"
    media_timeout_s: float = Field(default=2.0, gt=0)
    media_retries: int = Field(default=3, ge=0, le=5)
    media_breaker_failures: int = Field(default=5, ge=1)
    media_breaker_cooldown_s: float = Field(default=15.0, gt=0)
    # Секрет seed наборов дня: HMAC(secret, "<mode_key>|<day>"). Без него наборы не создаются.
    daily_seed_secret: SecretStr | None = None
    # Каталог сидов режимов: публикуются при старте relay только в local/ci.
    bootstrap_modes_dir: Path | None = None
    config_cache_ttl_s: float = Field(default=60.0, gt=0)
    mode_config_max_bytes: int = Field(default=65536, ge=1024)
    schedule_interval_s: float = Field(default=900.0, gt=0)
    schedule_jitter_s: float = Field(default=60.0, ge=0)
    abandon_interval_s: float = Field(default=600.0, gt=0)
    abandon_jitter_s: float = Field(default=60.0, ge=0)
    abandon_batch: int = Field(default=500, ge=1)
    bootstrap_interval_s: float = Field(default=3600.0, gt=0)
    # Ссылка в тексте «Поделиться» (D14); пусто — без ссылки.
    share_url: str = ""
    # Ссылки ревила кэшируются в процессе до истечения минус этот запас.
    reveal_link_margin_s: float = Field(default=10.0, ge=0)
    reveal_cache_size: int = Field(default=10000, ge=100)
    # Подпись в тексте «Поделиться» (название продукта).
    share_title: str = Field(default="OnCue", max_length=40)
    # Сколько кандидатов пула запрашивать у проекции за раз (этап 2 — весь пул).
    candidate_limit: int = Field(default=5000, ge=1, le=100000)
    # Окно, на сколько дней вперёд админ видит наборы, ограничено для GET /admin/v1/daily-sets.
    admin_daily_range_days: int = Field(default=62, ge=1, le=366)
    # Точный ответ на уровне 1 быстрее порога — флаг в лог для детектора аномалий (§9 п. 4).
    fast_exact_ms: int = Field(default=300, ge=0)


class GameLimits(BaseModel):
    """Лимиты частоты: ``GV_GAME_LIMITS__*`` (§9 п. 4)."""

    model_config = ConfigDict(frozen=True)

    guess_per_min: int = Field(default=60, ge=1)
    clue_per_min: int = Field(default=120, ge=1)
    sessions_per_hour: int = Field(default=60, ge=1)
    guest_sessions_per_ip_per_hour: int = Field(default=30, ge=1)


class Settings(ServiceSettings):
    """Настройки сервиса. Новые поля добавлять здесь с безопасными значениями по умолчанию."""

    service_name: str = "game"
    game: GameSettings = GameSettings()
    game_limits: GameLimits = GameLimits()
