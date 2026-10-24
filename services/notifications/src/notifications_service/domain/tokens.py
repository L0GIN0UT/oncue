"""Непрозрачные токены отписки (ADR 0048, план этапа 5 §9.8).

Токен - случайные 256 бит (``secrets.token_urlsafe``), выдаётся персонально получателю при
занятии доставки рассылки. В адресе нет ни ``user_id``, ни ``broadcast_id``; в БД хранится только
sha256 токена, поэтому утечка БД не даёт рабочих ссылок. Токен ничего не «подписывает»: его
владелец определяется поиском хеша.
"""

from __future__ import annotations

import hashlib
import secrets

TOKEN_BYTES = 32


def new_token() -> str:
    """Новый случайный токен отписки (43 символа base64url)."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """sha256-хеш токена (hex, 64 символа) - то, что хранится в БД.

    Любая строка (в т.ч. не ASCII и мусор) даёт хеш: неверный токен просто не находится.
    """
    return hashlib.sha256(token.encode("utf-8", errors="replace")).hexdigest()
