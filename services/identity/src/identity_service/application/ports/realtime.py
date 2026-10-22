"""Порт подписи connection-токенов Centrifugo (D9 плана этапа 4, ADR 0035)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class RealtimeTokenSigner(Protocol):
    """Подпись connection-JWT отдельным HMAC-секретом Centrifugo.

    Секрет не совпадает с ключами access-токенов (те асимметричные, JWKS): верификаторы
    сервисов принимают только EdDSA/ES256, поэтому realtime-токен не проходит как access.
    """

    def sign(self, claims: Mapping[str, Any]) -> str:
        """Подписать готовые claims и вернуть JWT (не логируется)."""
        ...
