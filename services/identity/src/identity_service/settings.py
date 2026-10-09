"""Настройки identity (переменные окружения ``GV_*``, вложенные — через ``__``).

Значения по умолчанию безопасны для production: Secure-cookie, Яндекс выключен, письма в
лог без ссылок. Секреты (ключи подписи, client secret, пароль SMTP) — только из окружения.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, SecretStr

from gv_service import ServiceSettings


class JwtSettings(BaseModel):
    """Ключи и время жизни токенов."""

    signing_keys: SecretStr | None = None
    """JSON ``[{"kid", "private_pem", "active", "not_after"?}]``; без него — только local/ci."""
    access_ttl_s: int = Field(default=900, ge=60, le=3600)
    refresh_ttl_s: int = Field(default=2_592_000, ge=3600)


class CookieSettings(BaseModel):
    """Параметры cookie сессии."""

    secure: bool = True
    domain: str | None = None
    refresh_path: str = "/api/identity/v1/auth"
    oauth_path: str = "/api/identity/v1/oauth"


class YandexSettings(BaseModel):
    """Яндекс ID (только привязка и вход через привязанный аккаунт)."""

    enabled: bool = False
    client_id: str = ""
    client_secret: SecretStr = SecretStr("")
    redirect_uri: str = ""
    authorize_url: str = "https://oauth.yandex.ru/authorize"
    token_url: str = "https://oauth.yandex.ru/token"  # noqa: S105 — URL, не секрет
    info_url: str = "https://login.yandex.ru/info"


class EmailSettings(BaseModel):
    """Письма."""

    backend: Literal["log", "smtp"] = "log"
    sender: str = "Guessverse <no-reply@guessverse.local>"
    verify_link: str = "https://guessverse.local/auth/verify-email?token={token}"
    reset_link: str = "https://guessverse.local/auth/reset-password?token={token}"


class SmtpSettings(BaseModel):
    """SMTP-сервер."""

    host: str = "localhost"
    port: int = 587
    username: str | None = None
    password: SecretStr | None = None
    start_tls: bool = True


class LoginLimitSettings(BaseModel):
    """Лимиты попыток."""

    per_ip_per_min: int = Field(default=10, ge=1)
    per_account_per_15min: int = Field(default=5, ge=1)


class Argon2Settings(BaseModel):
    """Параметры argon2id (минимум по §8 п.1: 64 МиБ, t ≥ 3)."""

    time_cost: int = Field(default=3, ge=1)
    memory_kib: int = Field(default=65_536, ge=8)
    parallelism: int = Field(default=1, ge=1)


class ConsentSettings(BaseModel):
    """Обязательные согласия ``kind:version,...``."""

    required: str = "terms:2026-10,privacy:2026-10,age_18:1"


class OAuthSettings(BaseModel):
    """Общие параметры OAuth."""

    return_to_prefixes: tuple[str, ...] = ("/",)
    state_ttl_s: int = Field(default=600, ge=60, le=1800)


class Settings(ServiceSettings):
    """Настройки сервиса. Новые поля добавлять здесь с безопасными значениями по умолчанию."""

    service_name: str = "identity"
    jwt: JwtSettings = JwtSettings()
    cookies: CookieSettings = CookieSettings()
    yandex: YandexSettings = YandexSettings()
    email: EmailSettings = EmailSettings()
    smtp: SmtpSettings = SmtpSettings()
    login_limit: LoginLimitSettings = LoginLimitSettings()
    argon2: Argon2Settings = Argon2Settings()
    consents: ConsentSettings = ConsentSettings()
    oauth: OAuthSettings = OAuthSettings()
    bootstrap_admin_emails: tuple[str, ...] = ()
    redis_key_prefix: str = ""
    """Пространство имён ключей Redis (тесты и несколько стендов на одном Redis).
    Для denylist ``sid`` сервисы-потребители должны использовать тот же префикс."""
