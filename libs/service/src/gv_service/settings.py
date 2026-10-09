"""Базовые настройки сервиса. Источник — только переменные окружения (12-factor).

Каждый сервис наследует ``ServiceSettings`` и добавляет свои поля. Префикс переменных —
``GV_``, вложенные поля через ``__``: ``GV_DATABASE_URL``, ``GV_RELAY__BATCH_SIZE``.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Окружение запуска."""

    LOCAL = "local"
    CI = "ci"
    STAGING = "staging"
    PRODUCTION = "production"


class ServiceSettings(BaseSettings):
    """Общие настройки процесса сервиса.

    Необязательные адреса инфраструктуры (``None``) означают «зависимость не используется»:
    соответствующие компоненты не создаются, проверки готовности не регистрируются.
    """

    model_config = SettingsConfigDict(
        env_prefix="GV_", env_nested_delimiter="__", extra="ignore", frozen=True
    )

    service_name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    # Безопасное значение по умолчанию: забытая или опечатанная переменная не должна
    # включить /docs и подробные ошибки на проде. Локально и в тестах окружение задаётся явно.
    environment: Environment = Environment.PRODUCTION
    log_level: str = "INFO"
    log_json: bool = True

    http_host: str = "0.0.0.0"  # noqa: S104 — внутри контейнера слушаем все интерфейсы
    http_port: int = Field(default=8000, ge=1, le=65535)
    # Отдельный внутренний порт метрик для всех процессов (api, consumer, worker, relay);
    # None — метрики не публикуются. Переход с /metrics на основном порту — этап 1, поток D.
    metrics_port: int | None = Field(default=9100, ge=1, le=65535)
    # Разрешённые Origin для мутаций с cookie-аутентификацией (CSRF), JSON-массив в env:
    # GV_PUBLIC_ORIGINS='["https://guessverse.ru"]'.
    public_origins: tuple[str, ...] = ()
    shutdown_timeout_s: float = Field(default=25.0, gt=0)

    database_url: SecretStr | None = None
    database_behind_pgbouncer: bool = False
    database_pool_size: int = Field(default=10, ge=1)
    redis_url: SecretStr | None = None
    kafka_bootstrap: str | None = None
    rabbitmq_url: SecretStr | None = None

    otlp_endpoint: str | None = None
    trace_sample_ratio: float = Field(default=1.0, ge=0, le=1)

    jwks_url: str | None = None
    jwt_issuer: str = "https://identity.guessverse.local"
    jwt_audience: str = "guessverse"

    @property
    def is_production(self) -> bool:
        """Боевое окружение: выключаем документацию API и подробные ошибки."""
        return self.environment is Environment.PRODUCTION
