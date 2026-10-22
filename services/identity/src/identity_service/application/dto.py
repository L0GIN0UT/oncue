"""Входные и выходные структуры use-cases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from identity_service.domain import ConsentRequirement


@dataclass(frozen=True, slots=True)
class Actor:
    """Кто выполняет действие (из проверенного access-токена)."""

    user_id: UUID
    roles: frozenset[str]
    perms: frozenset[str]
    session_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RegisterCommand:
    """Регистрация по никнейму, e-mail и паролю."""

    nickname: str
    email: str
    password: str
    consents: tuple[ConsentRequirement, ...]
    display_name: str | None = None
    locale: str | None = None
    ip: str | None = None


@dataclass(frozen=True, slots=True)
class ChallengeIssued:
    """Ответ на запрос кода: одинаков для существующего и несуществующего адреса (ADR 0041).

    Attributes:
        challenge_id: Идентификатор кода (для подавленного письма — фиктивный).
        purpose: ``register`` | ``login`` | ``reset`` | ``password_change``.
        code_length: Цифр в коде.
        expires_in_s: Сколько секунд код действует.
        resend_after_s: Через сколько секунд можно запросить повтор.
        binding_token: Значение одноразового cookie ``gv_bind`` (register, login): в тело
            ответа не попадает, API кладёт его в cookie.
    """

    challenge_id: UUID
    purpose: str
    code_length: int
    expires_in_s: int
    resend_after_s: int
    binding_token: str | None = None


@dataclass(frozen=True, slots=True)
class LinkedAccountView:
    """Привязанный аккаунт для профиля (без внешнего идентификатора)."""

    provider: str
    display: str | None
    linked_at: datetime


@dataclass(frozen=True, slots=True)
class UserView:
    """Данные пользователя для владельца аккаунта и админки."""

    id: UUID
    nickname: str
    email: str
    email_verified: bool
    display_name: str
    locale: str | None
    status: str
    roles: tuple[str, ...]
    perms: tuple[str, ...]
    created_at: datetime
    linked_accounts: tuple[LinkedAccountView, ...] = ()
    consents: tuple[ConsentRequirement, ...] = ()

    def to_json(self) -> dict[str, Any]:
        """JSON-объект (без согласий) — ответ admin-мутации, сохраняемый для идемпотентности."""
        return {
            "id": str(self.id),
            "nickname": self.nickname,
            "email": self.email,
            "email_verified": self.email_verified,
            "display_name": self.display_name,
            "locale": self.locale,
            "status": self.status,
            "roles": list(self.roles),
            "perms": list(self.perms),
            "created_at": self.created_at.isoformat(),
            "linked_accounts": [
                {"provider": a.provider, "display": a.display, "linked_at": a.linked_at.isoformat()}
                for a in self.linked_accounts
            ],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> UserView:
        """Обратно из ``to_json``."""
        return cls(
            id=UUID(data["id"]),
            nickname=data["nickname"],
            email=data["email"],
            email_verified=data["email_verified"],
            display_name=data["display_name"],
            locale=data["locale"],
            status=data["status"],
            roles=tuple(data["roles"]),
            perms=tuple(data["perms"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            linked_accounts=tuple(
                LinkedAccountView(
                    provider=a["provider"],
                    display=a["display"],
                    linked_at=datetime.fromisoformat(a["linked_at"]),
                )
                for a in data["linked_accounts"]
            ),
        )
