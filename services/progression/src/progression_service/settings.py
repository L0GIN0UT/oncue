"""Настройки progression (переменные окружения ``GV_*``).

Здесь — только эксплуатационные параметры процесса. Всё, что влияет на игру (опыт, уровни,
Версы, потолки, часовой пояс суток), живёт в версионируемом конфиге progression.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr

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
    # Каталог со стартовым конфигом (`config.yaml`): публикуется как версия 1 только в local/ci
    # и только пока нет ни одной опубликованной версии.
    bootstrap_config_dir: Path | None = None
    bootstrap_interval_s: float = Field(default=60.0, gt=0)
    # Магазин 5b (ADR 0045, 0046): бакет косметики и публичный префикс gateway для картинок.
    cosmetics_bucket: str = Field(default="gv-cosmetics", pattern=r"^[a-z0-9][a-z0-9.-]{2,62}$")
    cosmetics_base_url: str = Field(default="/cosmetics/", pattern=r"^(https?://[^/]+)?/[^?#]*/$")
    # Ключ HMAC хешей промокодов (ADR 0048). Вне local/ci обязателен: без него сервис не стартует.
    promo_hmac_key: SecretStr | None = Field(default=None, min_length=32)
    # Паки скрыты, пока биллинг выключен (ADR 0007); включает владелец, не код.
    billing_enabled: bool = False
    # Подсказка клиенту, когда лимитер попыток (Redis) недоступен и ввод кода закрыт (503).
    limiter_retry_after_s: int = Field(default=5, ge=1, le=300)


class S3Settings(BaseModel):
    """S3-совместимое хранилище (SeaweedFS на VPS, ADR 0021): ``GV_S3__*``."""

    model_config = ConfigDict(frozen=True)

    endpoint: str | None = None
    access_key: SecretStr | None = None
    secret_key: SecretStr | None = None
    region: str = "us-east-1"
    timeout_s: float = Field(default=10.0, gt=0)


class Settings(ServiceSettings):
    """Настройки сервиса. Новые поля добавлять здесь с безопасными значениями по умолчанию."""

    service_name: str = "progression"
    progression: ProgressionSettings = ProgressionSettings()
    s3: S3Settings = S3Settings()
    # Ключ кодов (ADR 0048) вне local/ci обязателен, но проверяется не здесь: инструменты
    # контрактов строят ``Settings()`` без env. См. ``infrastructure.adapters.hmac_key``.
