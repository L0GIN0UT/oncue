"""Настройки admin из переменных окружения (``GV_*``).

Общие поля процесса (БД, Kafka, JWKS, порт метрик) — из ``ServiceSettings``; собственные
поля админки — в группе ``GV_ADMIN__*``. Django-настройки (``admin_service.django_settings``)
строятся из этого объекта, а не читают окружение напрямую: один источник и одна валидация.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr

from gv_service import ServiceSettings

# Частные сети и loopback: доступ из внутренней сети и через VPN (docs/05-admin-panel.md §3).
DEFAULT_IP_ALLOWLIST = (
    "127.0.0.0/8",
    "::1/128",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
)


class AdminConfig(BaseModel):
    """Параметры админки (``GV_ADMIN__*``).

    Attributes:
        secret_key: Ключ Django (подпись сессий и CSRF). На проде обязателен.
        allowed_hosts: Допустимые ``Host`` (JSON-массив в env).
        csrf_trusted_origins: Origin, с которых разрешены POST (если админка за TLS-прокси).
        ip_allowlist: Сети (CIDR), из которых доступна админка; health-пробы — без проверки.
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
    ip_allowlist: tuple[str, ...] = DEFAULT_IP_ALLOWLIST
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


class Settings(ServiceSettings):
    """Настройки процесса admin."""

    service_name: str = "admin"
    admin: AdminConfig = Field(default_factory=AdminConfig)

    @property
    def effective_jwks_url(self) -> str:
        """JWKS identity: из ``GV_JWKS_URL`` или по адресу identity."""
        return self.jwks_url or f"{self.admin.identity_url.rstrip('/')}/.well-known/jwks.json"
