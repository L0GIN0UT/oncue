"""Настройки admin из переменных окружения (``GV_*``).

Общие поля процесса (БД, Kafka, JWKS, порт метрик) — из ``ServiceSettings``; собственные
поля админки — в группе ``GV_ADMIN__*``. Django-настройки (``admin_service.django_settings``)
строятся из этого объекта, а не читают окружение напрямую: один источник и одна валидация.
"""

from __future__ import annotations

import ipaddress

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from gv_service import Environment, ServiceSettings

# Частные сети и loopback — умолчание ТОЛЬКО для local/ci. На staging/production allowlist
# задаётся явно (офис, VPN): «вся частная сеть» там слишком широко (docs/05-admin-panel.md §3).
DEV_IP_ALLOWLIST = (
    "127.0.0.0/8",
    "::1/128",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
)
DEV_ENVIRONMENTS = frozenset({Environment.LOCAL, Environment.CI})


class AdminConfig(BaseModel):
    """Параметры админки (``GV_ADMIN__*``).

    Attributes:
        secret_key: Ключ Django (подпись сессий и CSRF). На проде обязателен.
        allowed_hosts: Допустимые ``Host`` (JSON-массив в env).
        csrf_trusted_origins: Origin, с которых разрешены POST (если админка за TLS-прокси).
        ip_allowlist: Сети (CIDR), из которых доступна админка; health-пробы — без проверки.
            Вне local/ci обязателен и не пуст (иначе процесс не стартует).
        session_enc_key: Ключ шифрования токенов identity в сессии (Fernet, urlsafe-base64
            32 байт). Не задан — выводится из ``secret_key`` через HKDF.
        login_attempts_limit: Попыток входа с одного IP за окно (сверх лимита identity).
        login_attempts_window_s: Окно лимита попыток входа.
        session_ttl_s: Время жизни сессии админки (8 ч).
        identity_url: Базовый URL identity для входа (``/internal/v1/admin-sessions``).
        service_urls: Базовые URL сервисов, чьи admin-API вызывает админка.
        service_timeout_s: Таймаут вызова сервиса.
        max_upload_mb: Предел размера загружаемого исходника (аудио, кадр, постер).
        forwarded_allow_ips: Кому uvicorn доверяет ``X-Forwarded-For`` (IP для allowlist).
    """

    secret_key: SecretStr | None = None
    allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "admin")
    csrf_trusted_origins: tuple[str, ...] = ()
    ip_allowlist: tuple[str, ...] | None = None
    session_enc_key: SecretStr | None = None
    login_attempts_limit: int = Field(default=10, ge=1)
    login_attempts_window_s: int = Field(default=900, ge=1)
    session_ttl_s: int = Field(default=28800, ge=60)
    identity_url: str = "http://identity:8000"
    service_urls: dict[str, str] = Field(
        default_factory=lambda: {
            "identity": "http://identity:8000",
            "catalog": "http://catalog:8000",
            "media_pipeline": "http://media-pipeline:8000",
        }
    )
    service_timeout_s: float = Field(default=10.0, gt=0)
    max_upload_mb: int = Field(default=50, ge=1)
    forwarded_allow_ips: str = "127.0.0.1"

    @field_validator("ip_allowlist")
    @classmethod
    def _valid_networks(cls, value: tuple[str, ...] | None) -> tuple[str, ...] | None:
        """Опечатка в CIDR — ошибка запуска, а не молча закрытая (или открытая) админка."""
        if value is None:
            return None
        if not value:
            raise ValueError("GV_ADMIN__IP_ALLOWLIST пуст: админка была бы недоступна")
        for network in value:
            ipaddress.ip_network(network, strict=False)
        return value

    @field_validator("session_enc_key")
    @classmethod
    def _valid_fernet_key(cls, value: SecretStr | None) -> SecretStr | None:
        """Некорректный ключ шифрования сессий — ошибка запуска, а не 500 при входе."""
        if value is not None:
            from cryptography.fernet import Fernet

            Fernet(value.get_secret_value().encode("ascii"))
        return value


class Settings(ServiceSettings):
    """Настройки процесса admin."""

    service_name: str = "admin"
    admin: AdminConfig = Field(default_factory=AdminConfig)

    @model_validator(mode="after")
    def _allowlist_required_outside_dev(self) -> Settings:
        if self.admin.ip_allowlist is None and self.environment not in DEV_ENVIRONMENTS:
            raise ValueError(
                f"GV_ADMIN__IP_ALLOWLIST обязателен в окружении {self.environment.value}"
            )
        return self

    @property
    def effective_ip_allowlist(self) -> tuple[str, ...]:
        """Allowlist IP: явный или (только local/ci) частные сети и loopback."""
        return self.admin.ip_allowlist or DEV_IP_ALLOWLIST

    @property
    def effective_jwks_url(self) -> str:
        """JWKS identity: из ``GV_JWKS_URL`` или по адресу identity."""
        return self.jwks_url or f"{self.admin.identity_url.rstrip('/')}/.well-known/jwks.json"
