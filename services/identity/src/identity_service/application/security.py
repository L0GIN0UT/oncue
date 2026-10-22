"""Вспомогательные функции безопасности application-слоя (без IO)."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import re
import unicodedata

from gv_core.errors import AppError, InvalidInputError, RateLimitedError
from identity_service.application.ports import RateDecision
from identity_service.domain import EmailAddress, Nickname

_IPV4_PREFIX = 24
_IPV6_PREFIX = 48
NO_IP = "-"


class OAuthStateError(AppError):
    """Неверный, истёкший или чужой ``state`` OAuth (HTTP 400)."""

    default_code = "auth.oauth_state_invalid"


def hash_secret(secret: str) -> str:
    """SHA-256 (hex) от секрета: в БД и Redis хранятся только хэши токенов."""
    return hashlib.sha256(secret.encode()).hexdigest()


def email_subject_hash(address: str) -> str:
    """Хэш субъекта кода для адреса (register/reset): в БД нет e-mail открытым текстом."""
    return hashlib.sha256(f"email:{address.casefold()}".encode()).hexdigest()


def mail_key(address: str) -> str:
    """Ключ лимита писем на адрес: хэш, без ПДн в Redis и в записи кода."""
    return hashlib.sha256(f"mail:{address.casefold()}".encode()).hexdigest()


def decoy_subject_hash(address: str) -> str:
    """Субъект «пустышки» для адреса: детерминирован, как у настоящего кода.

    Повторный запрос отменяет прежнюю пустышку, а суточный потолок неудач считается по ней
    так же, как по настоящему субъекту, — иначе по ``attempts_left`` и 429 адрес с аккаунтом
    отличался бы от адреса без него.
    """
    return hashlib.sha256(f"decoy:{address.casefold()}".encode()).hexdigest()


def user_subject_hash(user_id: object) -> str:
    """Хэш субъекта кода для пользователя (login/password_change)."""
    return hashlib.sha256(f"user:{user_id}".encode()).hexdigest()


def pkce_challenge(verifier: str) -> str:
    """``code_challenge`` для метода S256 (RFC 7636 §4.2)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def ip_subnet(ip: str | None, *, v4_bits: int, v6_bits: int) -> str | None:
    """Подсеть адреса (``None`` — адреса нет или он некорректен)."""
    if not ip:
        return None
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return None
    bits = v4_bits if address.version == 4 else v6_bits  # noqa: PLR2004
    return str(ipaddress.ip_network(f"{address}/{bits}", strict=False))


def ip_prefix(ip: str | None) -> str | None:
    """Сеть клиента вместо точного адреса (/24 для IPv4, /48 для IPv6) — минимизация ПДн."""
    return ip_subnet(ip, v4_bits=_IPV4_PREFIX, v6_bits=_IPV6_PREFIX)


def canonical_login(raw: str) -> str:
    """Каноническая форма логина для ключа лимитера.

    Те же правила, что при поиске аккаунта (``EmailAddress``/``Nickname``: NFKC) плюс
    casefold: «ａdmin», «𝐚dmin» и знак Кельвина дают тот же ключ, что и «admin».
    Некорректный логин — NFKC + casefold исходной строки.
    """
    try:
        if "@" in raw:
            return EmailAddress.parse(raw).value.casefold()
        return Nickname.parse(raw).value.casefold()
    except InvalidInputError:
        return unicodedata.normalize("NFKC", raw).strip().casefold()


def rate_key(scope: str, value: str) -> str:
    """Ключ лимитера: значение (IP, логин) только в виде хэша — без ПДн в Redis."""
    return f"rl:{scope}:{hashlib.sha256(value.lower().encode()).hexdigest()[:32]}"


def ensure_allowed(decision: RateDecision) -> None:
    """Бросить 429, если лимит исчерпан.

    Raises:
        RateLimitedError: ``rate.limited`` с ``details.retry_after``.
    """
    if not decision.allowed:
        raise RateLimitedError(
            "слишком много попыток, повторите позже",
            details={"retry_after": max(1, decision.retry_after_s)},
        )


_UNSAFE_PATH = re.compile(r"[\\\s]")


def safe_return_to(raw: str | None, prefixes: tuple[str, ...]) -> str:
    """Проверить путь возврата: только относительный и из allowlist (защита от open redirect).

    Raises:
        OAuthStateError: ``auth.return_to_invalid``.
    """
    value = raw or "/"
    invalid = OAuthStateError("недопустимый адрес возврата", code="auth.return_to_invalid")
    if (
        not value.startswith("/")
        or value.startswith("//")
        or _UNSAFE_PATH.search(value)
        or any(unicodedata.category(ch).startswith("C") for ch in value)
        or len(value) > 512  # noqa: PLR2004
    ):
        raise invalid
    path = value.split("?", 1)[0].split("#", 1)[0]
    if not any(path == p or path.startswith(p.rstrip("/") + "/") for p in prefixes):
        raise invalid
    return value
