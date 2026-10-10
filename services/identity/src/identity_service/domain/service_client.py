"""Клиенты сервисных токенов (client credentials, D13 плана этапа 2).

Клиент — другой сервис системы (``game``, ``admin``). Секрет хранится только как sha256:
секреты — 256-битные случайные строки, поэтому медленный KDF не нужен, а утечка
конфигурации identity не раскрывает сами секреты. Клиенту явно перечислены адресаты
(``audience``) и области (``scope``) для каждого из них — всё, что не перечислено, запрещено.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from gv_core.errors import InvalidInputError

CLIENT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
AUDIENCE_PATTERN = CLIENT_ID_PATTERN
SCOPE_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
SECRET_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ServiceClient:
    """Клиент сервисных токенов.

    Attributes:
        client_id: Идентификатор (``sub = svc:<client_id>`` в токене).
        secret_sha256: sha256 (hex, нижний регистр) секрета клиента.
        audiences: Разрешённые адресаты → области токена для каждого.
    """

    client_id: str
    secret_sha256: str
    audiences: Mapping[str, frozenset[str]]

    @classmethod
    def create(
        cls,
        client_id: str,
        secret_sha256: str,
        audiences: Mapping[str, Iterable[str]],
        *,
        forbidden_audiences: Iterable[str] = (),
    ) -> ServiceClient:
        """Проверить и создать клиента.

        Args:
            forbidden_audiences: Адресаты, которые нельзя выдавать сервисам (``aud``
                пользовательских токенов): сервисный токен не должен подходить публичным
                ручкам даже по ``aud``.

        Raises:
            InvalidInputError: ``service_client.invalid`` — некорректная конфигурация.
        """
        forbidden = frozenset(forbidden_audiences)
        if not CLIENT_ID_PATTERN.match(client_id):
            raise _invalid(f"некорректный client_id: {client_id!r}")
        if not SECRET_SHA256_PATTERN.match(secret_sha256):
            raise _invalid(f"{client_id}: secret_sha256 — 64 hex-символа в нижнем регистре")
        if not audiences:
            raise _invalid(f"{client_id}: не задан ни один адресат (audiences)")
        parsed: dict[str, frozenset[str]] = {}
        for audience, scopes in audiences.items():
            if not AUDIENCE_PATTERN.match(audience) or audience in forbidden:
                raise _invalid(f"{client_id}: недопустимый адресат {audience!r}")
            scope_set = frozenset(scopes)
            if not scope_set or not all(SCOPE_PATTERN.match(s) for s in scope_set):
                raise _invalid(f"{client_id}: некорректные области для {audience!r}")
            parsed[audience] = scope_set
        return cls(client_id, secret_sha256, MappingProxyType(parsed))

    def scopes_for(self, audience: str) -> frozenset[str] | None:
        """Области токена для адресата; ``None`` — адресат клиенту не разрешён."""
        return self.audiences.get(audience)


class ServiceClientRegistry:
    """Набор клиентов из конфигурации (без IO: меняется только рестартом).

    Raises:
        InvalidInputError: ``service_client.invalid`` — повтор ``client_id``.
    """

    def __init__(self, clients: Iterable[ServiceClient]) -> None:
        by_id: dict[str, ServiceClient] = {}
        for client in clients:
            if client.client_id in by_id:
                raise _invalid(f"client_id повторяется: {client.client_id}")
            by_id[client.client_id] = client
        self._clients = by_id

    def get(self, client_id: str) -> ServiceClient | None:
        """Клиент по идентификатору."""
        return self._clients.get(client_id)

    def __len__(self) -> int:
        return len(self._clients)


def _invalid(message: str) -> InvalidInputError:
    return InvalidInputError(message, code="service_client.invalid")
