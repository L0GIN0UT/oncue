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
class CodeConfig:
    """Одноразовые коды из писем и доверенные устройства (ADR 0041, ``GV_EMAIL_CODE__*``).

    Attributes:
        length: Цифр в коде.
        ttl_s: Время жизни кода.
        max_attempts: Вводов на один код.
        resend_after_s: Минимальный интервал между отправками одного кода.
        daily_fail_limit: Неудачных вводов в сутки на субъекта.
        request_per_ip_per_min: Запросов кода (вход с нового устройства, resend, смена пароля)
            с одного IP в минуту.
        confirm_per_ip_per_min: Вводов кода с одного IP в минуту.
        device_ttl_s: Срок доверия устройству (``device_cookie_ttl_days`` в секундах).
        max_lifetime_s: Абсолютный срок жизни записи кода от создания (resend не продлевает).
        check_concurrency: Одновременных проверок кода на процесс (пул соединений конечен).
        purge_retention_s / device_revoked_retention_s / purge_batch / purge_max_batches:
            Очистка просроченных кодов и отозванных устройств.
    """

    length: int = 6
    ttl_s: int = 600
    max_attempts: int = 5
    resend_after_s: int = 60
    daily_fail_limit: int = 20
    request_per_ip_per_min: int = 5
    confirm_per_ip_per_min: int = 20
    device_ttl_s: int = 15_552_000
    max_lifetime_s: int = 3600
    check_concurrency: int = 3
    purge_retention_s: int = 172_800
    device_revoked_retention_s: int = 604_800
    purge_batch: int = 1000
    purge_max_batches: int = 20


@dataclass(frozen=True, slots=True)
class AuthConfig:
    """Настройки входа, токенов и писем.

    Attributes:
        access_ttl_s: Время жизни access-токена (и записи в denylist ``sid``).
        refresh_ttl_s: Время жизни refresh-токена (скользящее: каждая ротация продлевает).
        required_consents: Согласия, без которых нельзя зарегистрироваться.
        bootstrap_admin_emails: Нормализованные адреса, получающие роль ``admin`` после
            подтверждения e-mail (первичная настройка).
        codes: Параметры одноразовых кодов и доверенных устройств.
        return_to_prefixes: Разрешённые относительные пути возврата после OAuth.
        oauth_state_ttl_s: Время жизни ``state``.
    """

    access_ttl_s: int = 900
    refresh_ttl_s: int = 2_592_000
    oauth_state_ttl_s: int = 600
    required_consents: tuple[ConsentRequirement, ...] = ()
    bootstrap_admin_emails: frozenset[str] = frozenset()
    return_to_prefixes: tuple[str, ...] = ("/",)
    password_policy: PasswordPolicy = field(default_factory=PasswordPolicy)
    limits: LimitConfig = field(default_factory=LimitConfig)
    codes: CodeConfig = field(default_factory=CodeConfig)
