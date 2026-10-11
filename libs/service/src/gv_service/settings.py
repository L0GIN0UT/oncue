"""Базовые настройки сервиса. Источник — только переменные окружения (12-factor).

Каждый сервис наследует ``ServiceSettings`` и добавляет свои поля. Префикс переменных —
``GV_``, вложенные поля через ``__``: ``GV_DATABASE_URL``, ``GV_RELAY__BATCH_SIZE``.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    """Окружение запуска."""

    LOCAL = "local"
    CI = "ci"
    STAGING = "staging"
    PRODUCTION = "production"


class RetentionSettings(BaseModel):
    """Сроки хранения служебных таблиц (docs/architecture/data.md §4).

    Attributes:
        outbox_days: Опубликованные события outbox и отметки inbox (``GV_RETENTION__OUTBOX_DAYS``).
        purge_interval_s: Пауза между запусками очистки в одной реплике.
        purge_jitter_s: Случайная добавка к паузе.
    """

    model_config = ConfigDict(frozen=True)

    outbox_days: float = Field(default=7, gt=0)
    purge_interval_s: float = Field(default=3600, gt=0)
    purge_jitter_s: float = Field(default=300, ge=0)


class IdempotencySettings(BaseModel):
    """Хранение ключей идемпотентности (``gv_db.idempotency``).

    Attributes:
        retention_h: Сколько часов хранить ключи (``GV_IDEMPOTENCY__RETENTION_H``); повтор
            запроса позже вернёт не сохранённый ответ, а выполнится заново.
    """

    model_config = ConfigDict(frozen=True)

    retention_h: float = Field(default=72, gt=0)


class InternalAuthSettings(BaseModel):
    """Проверка сервисных токенов на internal-ручках (D13 плана этапа 2).

    Attributes:
        audience: Ожидаемый ``aud`` сервисного токена (``GV_INTERNAL_AUTH__AUDIENCE``);
            ``None`` — имя сервиса. Не может совпадать с ``jwt_audience`` пользовательских
            токенов: иначе граница «пользователь/сервис» держалась бы только на форме ``sub``.
    """

    model_config = ConfigDict(frozen=True)

    audience: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$", max_length=64)


class ServiceAuthSettings(BaseModel):
    """Учётные данные сервиса для исходящих вызовов (client credentials, D13).

    Attributes:
        token_url: ``http://identity:8000/internal/v1/service-tokens``
            (``GV_SERVICE_AUTH__TOKEN_URL``).
        client_id: Идентификатор клиента в ``GV_SERVICE_CLIENTS`` identity; ``None`` —
            имя сервиса.
        client_secret: Секрет клиента (identity хранит только его sha256).
        timeout_s: Таймаут запроса к identity.
        refresh_ratio: Доля TTL, после которой токен обновляется.
        retry_backoff_s: Пауза между попытками обновления при отказе identity.
    """

    model_config = ConfigDict(frozen=True)

    token_url: str | None = None
    client_id: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    client_secret: SecretStr | None = None
    timeout_s: float = Field(default=2.0, gt=0, le=30)
    refresh_ratio: float = Field(default=0.5, gt=0, lt=1)
    retry_backoff_s: float = Field(default=5.0, ge=0, le=300)


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

    retention: RetentionSettings = RetentionSettings()
    idempotency: IdempotencySettings = IdempotencySettings()

    otlp_endpoint: str | None = None
    trace_sample_ratio: float = Field(default=1.0, ge=0, le=1)

    jwks_url: str | None = None
    jwt_issuer: str = "https://identity.guessverse.local"
    jwt_audience: str = "guessverse"
    internal_auth: InternalAuthSettings = InternalAuthSettings()
    service_auth: ServiceAuthSettings = ServiceAuthSettings()

    @property
    def internal_audience(self) -> str:
        """``aud`` сервисных токенов, адресованных этому сервису."""
        return self.internal_auth.audience or self.service_name

    @model_validator(mode="after")
    def _audiences_differ(self) -> ServiceSettings:
        if self.internal_audience == self.jwt_audience:
            raise ValueError(
                "GV_INTERNAL_AUTH__AUDIENCE не может совпадать с GV_JWT_AUDIENCE "
                "(пользовательские токены прошли бы internal-ручки по aud)"
            )
        return self

    @property
    def is_production(self) -> bool:
        """Боевое окружение: выключаем документацию API и подробные ошибки."""
        return self.environment is Environment.PRODUCTION
