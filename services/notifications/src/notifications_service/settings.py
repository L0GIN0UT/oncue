"""Настройки notifications (переменные окружения ``GV_*``)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from gv_service import ServiceSettings


class SmtpSettings(BaseModel):
    """SMTP-сервер (``GV_NOTIFICATIONS__SMTP__*``)."""

    host: str = "localhost"
    port: int = 587
    sender: str = "Guessverse <no-reply@guessverse.local>"
    username: str | None = None
    password: SecretStr | None = None
    start_tls: bool = True
    use_tls: bool = False
    """Неявный TLS (порт 465); взаимоисключающ со ``start_tls``."""
    timeout_s: float = Field(default=10.0, gt=0, le=120)

    @model_validator(mode="after")
    def _one_tls_mode(self) -> SmtpSettings:
        if self.use_tls and self.start_tls:
            raise ValueError("SMTP: use_tls и start_tls взаимоисключающие")
        return self


class NotificationsSettings(BaseModel):
    """Настройки сервиса (``GV_NOTIFICATIONS__*``).

    Attributes:
        types_path: Свой файл типов уведомлений вместо встроенного (``None`` - встроенный).
        unsubscribe_ttl_days: Срок жизни токена отписки с момента отправки письма (дни).
        public_base_url: База публичных ссылок (страница отписки ``{base}/unsubscribe?token=``).
        unsubscribe_attempts: Попыток отписки с одного IP за окно.
        unsubscribe_window_s: Окно лимита попыток.
        limiter_retry_after_s: ``Retry-After`` при недоступном Redis (fail-closed).
        broadcast_batch_size: Получателей рассылки в одной порции.
        unsubscribe_footer: Подпись письма рассылки со ссылкой отписки (плейсхолдер ``{link}``).
        email_backend: ``log`` (только local/ci), ``smtp``.
        identity_url: База identity для internal-ручки адресов (DNS compose/Kubernetes).
        identity_timeout_s: Таймаут вызова identity.
        smtp: SMTP-сервер.
    """

    types_path: Path | None = None
    unsubscribe_ttl_days: int = Field(default=90, ge=1, le=3650)
    public_base_url: str | None = Field(default=None, pattern=r"^https?://")
    unsubscribe_attempts: int = Field(default=10, ge=1, le=1000)
    unsubscribe_window_s: int = Field(default=600, ge=1, le=86_400)
    limiter_retry_after_s: int = Field(default=5, ge=1, le=300)
    broadcast_batch_size: int = Field(default=100, ge=1, le=1000)
    fanout_batch_size: int = Field(default=200, ge=1, le=2000)
    """Игроков в одной пачке массовой доставки (``audience``)."""
    type_flags_cache_ttl_s: float = Field(default=15.0, ge=0, le=3600)
    """Кэш выключателей типов в процессе (секунды); 0 - без кэша. Другой процесс увидит
    решение админа не позже чем через TTL."""
    unsubscribe_footer: str = Field(
        default="Чтобы отписаться от рассылки, перейдите по ссылке: {link}",
        min_length=1,
        max_length=500,
    )
    email_backend: Literal["log", "smtp"] = "log"
    identity_url: str = Field(default="http://identity:8000", pattern=r"^https?://")
    identity_timeout_s: float = Field(default=5.0, gt=0, le=60)
    smtp: SmtpSettings = SmtpSettings()

    @field_validator("types_path", "public_base_url", mode="before")
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        # compose передаёт `${VAR:-}`: пустая строка = «не задано».
        return None if value == "" else value

    @field_validator("unsubscribe_footer")
    @classmethod
    def _footer_has_link(cls, value: str) -> str:
        # Письмо рассылки без ссылки отписки отправляться не должно никогда.
        if "{link}" not in value:
            raise ValueError("unsubscribe_footer должен содержать {link}")
        return value


class Settings(ServiceSettings):
    """Настройки сервиса. Новые поля добавлять здесь с безопасными значениями по умолчанию."""

    service_name: str = "notifications"
    notifications: NotificationsSettings = NotificationsSettings()
