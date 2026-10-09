"""Вспомогательные функции безопасности application-слоя (без IO)."""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import re
import unicodedata

from gv_core.errors import AppError, RateLimitedError
from identity_service.application.ports import RateDecision

_IPV4_PREFIX = 24
_IPV6_PREFIX = 48


class OAuthStateError(AppError):
    """Неверный, истёкший или чужой ``state`` OAuth (HTTP 400)."""

    default_code = "auth.oauth_state_invalid"


def hash_secret(secret: str) -> str:
    """SHA-256 (hex) от секрета: в БД и Redis хранятся только хэши токенов."""
    return hashlib.sha256(secret.encode()).hexdigest()


def pkce_challenge(verifier: str) -> str:
    """``code_challenge`` для метода S256 (RFC 7636 §4.2)."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def ip_prefix(ip: str | None) -> str | None:
    """Сеть клиента вместо точного адреса (/24 для IPv4, /48 для IPv6) — минимизация ПДн."""
    if not ip:
        return None
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return None
    bits = _IPV4_PREFIX if address.version == 4 else _IPV6_PREFIX  # noqa: PLR2004
    return str(ipaddress.ip_network(f"{address}/{bits}", strict=False))


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
