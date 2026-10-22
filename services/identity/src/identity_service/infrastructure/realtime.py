"""Подпись connection-токенов Centrifugo (D9 плана этапа 4, ADR 0035)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import jwt

# Тот же минимум, что у RealtimeSettings (gv_service): короткий HMAC-секрет подбирается
# офлайн по любому перехваченному токену. Повтор здесь — защита в глубину для сборки
# подписанта в обход настроек.
_MIN_SECRET_LEN = 32


class HmacRealtimeTokenSigner:
    """HS256 на ``GV_REALTIME__CONNECTION_TOKEN_SECRET``.

    Centrifugo v6 проверяет connection-токен ключом ``client.token.hmac_secret_key``.
    Заголовок без ``kid``: ротация — сменой секрета у обоих (короткий TTL токена делает её
    безболезненной: клиенты переподключатся с новым токеном).
    """

    def __init__(self, secret: str) -> None:
        if len(secret) < _MIN_SECRET_LEN:
            raise ValueError(f"секрет connection-токенов короче {_MIN_SECRET_LEN} символов")
        self._secret = secret

    def sign(self, claims: Mapping[str, Any]) -> str:
        """Подписать claims (HS256)."""
        return jwt.encode(dict(claims), self._secret, algorithm="HS256")

    def __repr__(self) -> str:
        # Секрет не должен попасть в логи/трейсбеки через repr.
        return "HmacRealtimeTokenSigner(<secret>)"
