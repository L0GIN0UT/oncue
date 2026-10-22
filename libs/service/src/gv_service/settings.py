"""Базовые настройки сервиса. Источник — только переменные окружения (12-factor).

Каждый сервис наследует ``ServiceSettings`` и добавляет свои поля. Префикс переменных —
``GV_``, вложенные поля через ``__``: ``GV_DATABASE_URL``, ``GV_RELAY__BATCH_SIZE``.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
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


class UgcSettings(BaseModel):
    """Пользовательский текст (ADR 0030): ``GV_UGC__*`` сервисов с UGC (collections, library).

    Attributes:
        denylist_path: Свой файл denylist (формат ``gv_text.ugc.UgcPolicy``), заменяет
            встроенный целиком (``GV_UGC__DENYLIST_PATH``); пусто или ``None`` — встроенный
            ``gv_text/data/ugc_denylist.txt``.
    """

    model_config = ConfigDict(frozen=True)

    denylist_path: Path | None = None

    @field_validator("denylist_path", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        # compose передаёт `${GV_UGC__DENYLIST_PATH:-}`: пустая строка = «не задан».
        return None if value == "" else value


_MIN_SECRET_LEN = 32


class RealtimeSettings(BaseModel):
    """Centrifugo (D9 этапа 4): ``GV_REALTIME__*`` сервисов с ``realtime = true`` в реестре.

    Публикует только бэкенд (server API с ключом), клиенты в каналы не пишут. Секреты токенов
    у сервисов разные: identity подписывает connection-токены, match — subscription-токены
    каналов комнат; поэтому оба поля необязательны, а нужное проверяет сам сервис.

    Attributes:
        api_url: Адрес server API, ``http://centrifugo:8000/api`` (``GV_REALTIME__API_URL``).
        api_key: Ключ server API (``GV_REALTIME__API_KEY``).
        connection_token_secret: HMAC-секрет connection-токенов (только identity,
            ``GV_REALTIME__CONNECTION_TOKEN_SECRET``).
        subscription_token_secret: HMAC-секрет subscription-токенов (только match,
            ``GV_REALTIME__SUBSCRIPTION_TOKEN_SECRET``).
        token_audience: ``aud`` токенов; совпадает с ``client.token.audience`` и
            ``client.subscription_token.audience`` в ``infra/centrifugo/config.json``.
        token_ttl_s: Время жизни выдаваемых токенов; не больше 10 минут (D9 этапа 4).
        timeout_s: Таймаут запроса к server API.
    """

    model_config = ConfigDict(frozen=True)

    api_url: str = Field(pattern=r"^https?://")
    api_key: SecretStr
    connection_token_secret: SecretStr | None = None
    subscription_token_secret: SecretStr | None = None
    token_audience: str = Field(default="centrifugo", min_length=1, max_length=64)
    token_ttl_s: int = Field(default=600, ge=60, le=600)
    timeout_s: float = Field(default=2.0, gt=0, le=30)

    @field_validator("api_key", "connection_token_secret", "subscription_token_secret")
    @classmethod
    def _strong_secret(cls, value: SecretStr | None) -> SecretStr | None:
        # Короткий HMAC-секрет подбирается офлайн по любому перехваченному токену.
        if value is not None and len(value.get_secret_value()) < _MIN_SECRET_LEN:
            raise ValueError(f"секрет Centrifugo короче {_MIN_SECRET_LEN} символов")
        return value

    @model_validator(mode="after")
    def _secrets_differ(self) -> RealtimeSettings:
        conn, sub = self.connection_token_secret, self.subscription_token_secret
        if (
            conn is not None
            and sub is not None
            and conn.get_secret_value() == sub.get_secret_value()
        ):
            raise ValueError("секреты connection- и subscription-токенов должны различаться (D9)")
        return self


class RunnersSettings(BaseModel):
    """Остановка долгоживущих компонентов процесса ``rooms`` (``GV_RUNNERS__*``, D6 этапа 4).

    Сумма таймаутов должна укладываться в ``shutdown_timeout_s`` и ``stop_grace_period``
    контейнера, иначе handoff оборвёт SIGKILL.

    Attributes:
        stop_timeout_s: Сколько ждать, пока ``Runner.run`` вернётся после сигнала остановки;
            дальше задача отменяется.
        handoff_timeout_s: Сколько ждать ``Runner.handoff``; дальше хук отменяется, а
            незавершённую передачу добирает механизм аренды (TTL lease).
    """

    model_config = ConfigDict(frozen=True)

    stop_timeout_s: float = Field(default=5.0, gt=0, le=60)
    handoff_timeout_s: float = Field(default=10.0, gt=0, le=60)


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
    # Сколько задач RabbitMQ воркер обрабатывает одновременно (prefetch на очередь). Тяжёлым
    # задачам (ffmpeg в media-pipeline) нужно меньше, иначе пик памяти выходит за лимит
    # контейнера и процесс убивает OOM: GV_TASK_PREFETCH.
    task_prefetch: int = Field(default=10, ge=1, le=100)

    retention: RetentionSettings = RetentionSettings()
    idempotency: IdempotencySettings = IdempotencySettings()

    otlp_endpoint: str | None = None
    trace_sample_ratio: float = Field(default=1.0, ge=0, le=1)

    jwks_url: str | None = None
    jwt_issuer: str = "https://identity.guessverse.local"
    jwt_audience: str = "guessverse"
    internal_auth: InternalAuthSettings = InternalAuthSettings()
    service_auth: ServiceAuthSettings = ServiceAuthSettings()
    # None — сервис не работает с Centrifugo (в реестре realtime = false).
    realtime: RealtimeSettings | None = None
    runners: RunnersSettings = RunnersSettings()

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

    @model_validator(mode="after")
    def _runners_fit_shutdown(self) -> ServiceSettings:
        budget = self.runners.stop_timeout_s + self.runners.handoff_timeout_s
        if budget >= self.shutdown_timeout_s:
            raise ValueError(
                "GV_RUNNERS__STOP_TIMEOUT_S + GV_RUNNERS__HANDOFF_TIMEOUT_S должны быть меньше "
                "GV_SHUTDOWN_TIMEOUT_S (остановка комнат не успеет до SIGKILL)"
            )
        return self

    @property
    def is_production(self) -> bool:
        """Боевое окружение: выключаем документацию API и подробные ошибки."""
        return self.environment is Environment.PRODUCTION
