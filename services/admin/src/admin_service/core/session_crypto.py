"""Шифрование токенов identity в серверной сессии (``django_session``).

Сессия лежит в БД админки открытым (подписанным, но не зашифрованным) JSON: утечка дампа
или реплики выдала бы действующие refresh-токены администраторов. Поэтому токены хранятся
зашифрованными Fernet (AES-128-CBC + HMAC-SHA256). Ключ — ``GV_ADMIN__SESSION_ENC_KEY``
или, если он не задан, выводится из ``SECRET_KEY`` через HKDF-SHA256 (отдельный ``info``:
ключ подписи Django и ключ шифрования не совпадают). Смена ключа — все сессии становятся
недействительными (повторный вход), а не ошибкой сервера.
"""

from __future__ import annotations

import base64
from functools import cache

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from django.conf import settings

from admin_service.core.service_client import SessionExpiredError

_HKDF_INFO = b"guessverse-admin:session-tokens:v1"


def derive_key(secret: str) -> bytes:
    """Ключ Fernet из секрета Django (HKDF-SHA256, 32 байта)."""
    raw = HKDF(algorithm=SHA256(), length=32, salt=None, info=_HKDF_INFO).derive(
        secret.encode("utf-8")
    )
    return base64.urlsafe_b64encode(raw)


@cache
def _fernet(explicit_key: str | None, secret: str) -> Fernet:
    return Fernet(explicit_key.encode("ascii") if explicit_key else derive_key(secret))


def _current() -> Fernet:
    return _fernet(settings.GV_SESSION_ENC_KEY, settings.SECRET_KEY)


def encrypt(token: str) -> str:
    """Зашифровать токен для хранения в сессии."""
    return _current().encrypt(token.encode("utf-8")).decode("ascii")


def decrypt(blob: object) -> str:
    """Расшифровать токен из сессии.

    Raises:
        SessionExpiredError: значение не строка, подделано или зашифровано другим ключом.
    """
    if not isinstance(blob, str):
        raise SessionExpiredError
    try:
        return _current().decrypt(blob.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeError) as exc:
        raise SessionExpiredError from exc
