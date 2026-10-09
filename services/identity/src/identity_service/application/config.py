"""Параметры use-cases identity (собираются из настроек в composition root)."""

from __future__ import annotations

from dataclasses import dataclass, field

from identity_service.domain import ConsentRequirement, PasswordPolicy


@dataclass(frozen=True, slots=True)
class LimitConfig:
    """Лимиты частоты (docs/plans/stage-1.md §8 п.1). Значения — из настроек.

    Attributes:
        login_per_ip / login_ip_window_s: Попытки проверки пароля с одного IP.
        login_per_account_subnet: Неудачи по аккаунту из одной подсети (/24 IPv4, /64 IPv6)
            за ``login_account_window_s`` — чужие попытки не блокируют владельца из другой
            сети.
        login_per_account: Общий мягкий лимит по аккаунту из всех сетей (распределённый
            перебор).
        subnet_v4_bits / subnet_v6_bits: Размер подсети для счётчика аккаунта.
        email_per_address / email_address_window_s: Писем на один адрес (защита жертвы от
            рассылки через register/forgot).
    """

    login_per_ip: int = 10
    login_ip_window_s: int = 60
    login_per_account_subnet: int = 5
    login_per_account: int = 50
    login_account_window_s: int = 900
    subnet_v4_bits: int = 24
    subnet_v6_bits: int = 64
    register_per_ip_per_min: int = 10
    reset_per_ip_per_min: int = 5
    email_per_address: int = 5
    email_address_window_s: int = 3600


@dataclass(frozen=True, slots=True)
class AuthConfig:
    """Настройки входа, токенов и писем.

    Attributes:
        access_ttl_s: Время жизни access-токена (и записи в denylist ``sid``).
        refresh_ttl_s: Время жизни refresh-токена (скользящее: каждая ротация продлевает).
        verify_ttl_s / reset_ttl_s: Время жизни ссылок из писем.
        required_consents: Согласия, без которых нельзя зарегистрироваться.
        bootstrap_admin_emails: Нормализованные адреса, получающие роль ``admin`` после
            подтверждения e-mail (первичная настройка).
        verify_link / reset_link: Шаблоны ссылок с ``{token}``.
        return_to_prefixes: Разрешённые относительные пути возврата после OAuth.
        oauth_state_ttl_s: Время жизни ``state``.
    """

    access_ttl_s: int = 900
    refresh_ttl_s: int = 2_592_000
    verify_ttl_s: int = 86_400
    reset_ttl_s: int = 3_600
    oauth_state_ttl_s: int = 600
    required_consents: tuple[ConsentRequirement, ...] = ()
    bootstrap_admin_emails: frozenset[str] = frozenset()
    verify_link: str = "https://guessverse.local/auth/verify-email?token={token}"
    reset_link: str = "https://guessverse.local/auth/reset-password?token={token}"
    return_to_prefixes: tuple[str, ...] = ("/",)
    password_policy: PasswordPolicy = field(default_factory=PasswordPolicy)
    limits: LimitConfig = field(default_factory=LimitConfig)
