"""Выдача сессий: сессия + refresh-токен + access-токен."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from gv_core.clock import Clock
from gv_core.errors import ForbiddenError
from gv_core.ids import uuid7
from identity_service.application.ports import (
    IdentityUnitOfWork,
    SecretGenerator,
    TokenIssuer,
)
from identity_service.application.security import hash_secret, ip_prefix
from identity_service.domain import RefreshToken, RoleMatrix, Session, SessionKind, User

REFRESH_TOKEN_BYTES = 32
ACCOUNT_BANNED = "auth.account_banned"


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """Результат входа или refresh. Секреты отдаются клиенту и больше нигде не хранятся.

    ``identity_version`` — версия агрегата User на момент выдачи (та же, что в событиях
    ``UserRolesChanged``): admin по ней отбрасывает события старше своего снимка прав.
    """

    session_id: UUID
    user_id: UUID
    access_token: str
    expires_in: int
    refresh_token: str
    roles: frozenset[str]
    perms: frozenset[str]
    display_name: str
    identity_version: int
    is_guest: bool = False
    refresh_ttl_s: int | None = None
    """Срок refresh-токена этой сессии, если он отличается от общего (гость)."""


class SessionIssuer:
    """Создаёт сессии и выпускает токены (общая часть входа паролем, через Яндекс, refresh)."""

    def __init__(
        self,
        tokens: TokenIssuer,
        secrets: SecretGenerator,
        clock: Clock,
        *,
        refresh_ttl_s: int,
    ) -> None:
        self._tokens = tokens
        self._secrets = secrets
        self._clock = clock
        self._refresh_ttl = timedelta(seconds=refresh_ttl_s)

    @property
    def refresh_ttl(self) -> timedelta:
        """Время жизни refresh-токена."""
        return self._refresh_ttl

    def new_refresh_secret(self) -> tuple[str, str]:
        """Новый секрет refresh-токена (32 байта) и его хэш."""
        secret = self._secrets.token(REFRESH_TOKEN_BYTES)
        return secret, hash_secret(secret)

    async def open(
        self,
        uow: IdentityUnitOfWork,
        user: User,
        matrix: RoleMatrix,
        *,
        kind: SessionKind,
        ip: str | None,
    ) -> IssuedSession:
        """Создать сессию в транзакции ``uow`` (коммит — за вызывающим).

        Raises:
            ForbiddenError: ``auth.account_banned`` - на аккаунт наложена санкция ACCOUNT.
        """
        mask = await self.sanctions_mask(uow, user.id)
        now = self._clock.now()
        session = Session(
            id=uuid7(),
            user_id=user.id,
            kind=kind,
            created_at=now,
            last_seen_at=now,
            ip_prefix=ip_prefix(ip),
        )
        secret, token_hash = self.new_refresh_secret()
        await uow.sessions.add(session)
        await uow.sessions.add_refresh(
            RefreshToken(
                id=uuid7(),
                session_id=session.id,
                token_hash=token_hash,
                parent_id=None,
                created_at=now,
                expires_at=now + self._refresh_ttl,
            )
        )
        return self.access(user, matrix, session.id, refresh_secret=secret, sanctions=mask)

    async def sanctions_mask(self, uow: IdentityUnitOfWork, user_id: UUID) -> int:
        """Маска claim ``sanctions`` для нового токена (истёкшие записи не входят).

        Raises:
            ForbiddenError: ``auth.account_banned`` (ADR 0052) - без причины и срока в ответе;
                проверяется после аутентификации, поэтому факт бана не раскрывается чужим.
        """
        snapshot = await uow.user_sanctions.get(user_id)
        if snapshot is None:
            return 0
        now = self._clock.now()
        if snapshot.banned_at(now):
            raise ForbiddenError("доступ к аккаунту ограничен", code=ACCOUNT_BANNED)
        return snapshot.mask_at(now)

    def access(
        self,
        user: User,
        matrix: RoleMatrix,
        session_id: UUID,
        *,
        refresh_secret: str,
        sanctions: int = 0,
    ) -> IssuedSession:
        """Выпустить access-токен с актуальными ролями, правами и маской санкций."""
        perms = matrix.effective_perms(user.roles)
        issued = self._tokens.issue_access(
            user_id=user.id,
            session_id=session_id,
            roles=sorted(user.roles),
            perms=sorted(perms),
            sanctions=sanctions,
        )
        return IssuedSession(
            session_id=session_id,
            user_id=user.id,
            access_token=issued.token,
            expires_in=issued.expires_in,
            refresh_token=refresh_secret,
            roles=user.roles,
            perms=perms,
            display_name=user.display_name.value,
            identity_version=user.version,
        )
