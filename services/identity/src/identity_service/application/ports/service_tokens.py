"""Порт выпуска сервисных токенов (D13 плана этапа 2)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class IssuedServiceToken:
    """Подписанный сервисный токен.

    Attributes:
        token: JWT; не логируется.
        expires_in: Время жизни, секунд.
        token_id: ``jti`` (для журнала вместо самого токена).
    """

    token: str
    expires_in: int
    token_id: str


class ServiceTokenSigner(Protocol):
    """Подпись сервисного JWT теми же ключами, что и пользовательские токены."""

    def issue_service(
        self, *, client_id: str, audience: str, scopes: Iterable[str], ttl_s: int
    ) -> IssuedServiceToken:
        """Выпустить токен: ``sub = svc:<client_id>``, ``aud``, ``scope``, ``jti``.

        Без ``sid``, ``roles`` и ``perms``: сервисный токен не описывает пользователя.
        """
        ...
