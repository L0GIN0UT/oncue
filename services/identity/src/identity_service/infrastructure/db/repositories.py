"""Репозитории identity на SQLAlchemy Core и Unit of Work с ними."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from types import TracebackType
from typing import Any, Self
from uuid import UUID

from sqlalchemy import Row, and_, delete, insert, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gv_core.clock import Clock
from gv_core.domain import AggregateRoot
from gv_core.errors import ConflictError
from gv_db.idempotency import SqlIdempotencyStore
from gv_db.uow import SqlAlchemyUnitOfWork
from gv_messaging.outbox import OutboxWriter
from identity_service.application.ports import EmailToken, EmailTokenPurpose, RoleChange
from identity_service.domain import (
    AuthIdentity,
    Consent,
    DisplayName,
    EmailAddress,
    Nickname,
    Provider,
    RefreshToken,
    RoleMatrix,
    Session,
    SessionKind,
    User,
    UserStatus,
)
from identity_service.infrastructure.db.tables import (
    TABLES,
    auth_identities,
    email_tokens,
    idempotency_keys,
    password_credentials,
    permissions,
    refresh_tokens,
    role_changes,
    role_permissions,
    roles,
    sessions,
    user_roles,
    users,
)
from identity_service.infrastructure.db.tables import (
    consents as consents_table,
)


def _constraint(exc: IntegrityError) -> str:
    """Имя нарушенного ограничения (asyncpg кладёт его в исходное исключение)."""
    for candidate in (exc.orig, getattr(exc.orig, "__cause__", None)):
        name = getattr(candidate, "constraint_name", None)
        if isinstance(name, str):
            return name
    return ""


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class SqlUserRepository:
    """Пользователи и их роли."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, user: User) -> None:
        """Вставить пользователя; конфликт ника или e-mail — ``ConflictError``."""
        try:
            async with self._s.begin_nested():
                await self._s.execute(insert(users).values(**self._row(user)))
        except IntegrityError as exc:
            name = _constraint(exc)
            code = "user.email_taken" if "email" in name else "user.nickname_taken"
            raise ConflictError("аккаунт уже существует", code=code) from exc
        await self._write_roles(user)
        user.persisted_version = user.version

    async def get(self, user_id: UUID) -> User | None:
        """По id."""
        return await self._one(users.c.id == user_id)

    async def find_by_email(self, email: EmailAddress) -> User | None:
        """По e-mail (citext — без учёта регистра)."""
        return await self._one(users.c.email == email.value)

    async def find_by_nickname(self, nickname: Nickname) -> User | None:
        """По никнейму (citext)."""
        return await self._one(users.c.nickname == nickname.value)

    async def save(self, user: User) -> None:
        """Обновить с оптимистичной блокировкой по ``version``."""
        result = await self._s.execute(
            update(users)
            .where(users.c.id == user.id, users.c.version == user.persisted_version)
            .values(**{k: v for k, v in self._row(user).items() if k != "id"})
        )
        if result.rowcount != 1:  # type: ignore[attr-defined]  # CursorResult у UPDATE
            raise ConflictError("данные пользователя изменились, повторите", code="user.conflict")
        await self._write_roles(user)
        user.persisted_version = user.version

    async def search(self, query: str | None, *, after: UUID | None, limit: int) -> list[User]:
        """Страница по возрастанию id с фильтром по префиксу ника или e-mail."""
        stmt = select(users).order_by(users.c.id).limit(limit)
        if after is not None:
            stmt = stmt.where(users.c.id > after)
        if query:
            pattern = _escape_like(query) + "%"
            stmt = stmt.where(
                or_(
                    users.c.nickname.ilike(pattern, escape="\\"),
                    users.c.email.ilike(pattern, escape="\\"),
                )
            )
        rows = (await self._s.execute(stmt)).all()
        role_map = await self._roles_for([r.id for r in rows])
        return [self._user(r, role_map.get(r.id, frozenset())) for r in rows]

    async def ids_with_roles(self, role_names: Iterable[str]) -> list[UUID]:
        """Активные пользователи с любой из ролей."""
        names = list(role_names)
        if not names:
            return []
        stmt = (
            select(user_roles.c.user_id)
            .join(users, users.c.id == user_roles.c.user_id)
            .where(user_roles.c.role.in_(names), users.c.status == UserStatus.ACTIVE.value)
            .distinct()
        )
        return list((await self._s.scalars(stmt)).all())

    async def _one(self, condition: Any) -> User | None:
        row = (await self._s.execute(select(users).where(condition))).first()
        if row is None:
            return None
        role_map = await self._roles_for([row.id])
        return self._user(row, role_map.get(row.id, frozenset()))

    async def _roles_for(self, ids: list[UUID]) -> dict[UUID, frozenset[str]]:
        if not ids:
            return {}
        rows = await self._s.execute(
            select(user_roles.c.user_id, user_roles.c.role).where(user_roles.c.user_id.in_(ids))
        )
        result: dict[UUID, set[str]] = {}
        for user_id, role in rows:
            result.setdefault(user_id, set()).add(role)
        return {k: frozenset(v) for k, v in result.items()}

    async def _write_roles(self, user: User) -> None:
        await self._s.execute(delete(user_roles).where(user_roles.c.user_id == user.id))
        if user.roles:
            await self._s.execute(
                insert(user_roles), [{"user_id": user.id, "role": r} for r in sorted(user.roles)]
            )

    @staticmethod
    def _row(user: User) -> dict[str, Any]:
        return {
            "id": user.id,
            "nickname": user.nickname.value,
            "email": user.email.value,
            "email_verified_at": user.email_verified_at,
            "display_name": user.display_name.value,
            "locale": user.locale,
            "status": user.status.value,
            "roles_version": user.roles_version,
            "version": user.version,
            "created_at": user.created_at,
            "deleted_at": user.deleted_at,
        }

    @staticmethod
    def _user(row: Row[Any], role_names: frozenset[str]) -> User:
        return User(
            row.id,
            nickname=Nickname(row.nickname),
            email=EmailAddress(row.email),
            display_name=DisplayName(row.display_name),
            locale=row.locale,
            status=UserStatus(row.status),
            roles=role_names,
            roles_version=row.roles_version,
            email_verified_at=row.email_verified_at,
            created_at=row.created_at,
            deleted_at=row.deleted_at,
            version=row.version,
        )


class SqlCredentialRepository:
    """Хэши паролей."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_password_hash(self, user_id: UUID) -> str | None:
        """Хэш пароля."""
        value: str | None = await self._s.scalar(
            select(password_credentials.c.password_hash).where(
                password_credentials.c.user_id == user_id
            )
        )
        return value

    async def set_password_hash(self, user_id: UUID, password_hash: str, now: datetime) -> None:
        """Создать или заменить."""
        stmt = pg_insert(password_credentials).values(
            user_id=user_id, password_hash=password_hash, updated_at=now
        )
        await self._s.execute(
            stmt.on_conflict_do_update(
                index_elements=[password_credentials.c.user_id],
                set_={"password_hash": password_hash, "updated_at": now},
            )
        )

    async def delete(self, user_id: UUID) -> None:
        """Удалить."""
        await self._s.execute(
            delete(password_credentials).where(password_credentials.c.user_id == user_id)
        )


class SqlSessionRepository:
    """Сессии и refresh-токены."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, session: Session) -> None:
        """Новая сессия."""
        await self._s.execute(insert(sessions).values(**self._session_row(session)))

    async def get(self, session_id: UUID) -> Session | None:
        """По id."""
        row = (await self._s.execute(select(sessions).where(sessions.c.id == session_id))).first()
        return self._session(row) if row else None

    async def save(self, session: Session) -> None:
        """Сохранить изменения."""
        values = self._session_row(session)
        await self._s.execute(update(sessions).where(sessions.c.id == session.id).values(**values))

    async def add_refresh(self, token: RefreshToken) -> None:
        """Новый refresh-токен."""
        await self._s.execute(insert(refresh_tokens).values(**self._token_row(token)))

    async def find_refresh_for_update(self, token_hash: str) -> RefreshToken | None:
        """С блокировкой строки — параллельные refresh одним токеном сериализуются."""
        row = (
            await self._s.execute(
                select(refresh_tokens)
                .where(refresh_tokens.c.token_hash == token_hash)
                .with_for_update()
            )
        ).first()
        if row is None:
            return None
        return RefreshToken(
            id=row.id,
            session_id=row.session_id,
            token_hash=row.token_hash,
            parent_id=row.parent_id,
            created_at=row.created_at,
            expires_at=row.expires_at,
            used_at=row.used_at,
        )

    async def save_refresh(self, token: RefreshToken) -> None:
        """Сохранить изменения токена."""
        await self._s.execute(
            update(refresh_tokens)
            .where(refresh_tokens.c.id == token.id)
            .values(used_at=token.used_at)
        )

    async def active_for_user(self, user_id: UUID) -> list[Session]:
        """Неотозванные сессии."""
        rows = await self._s.execute(
            select(sessions).where(sessions.c.user_id == user_id, sessions.c.revoked_at.is_(None))
        )
        return [self._session(r) for r in rows]

    @staticmethod
    def _session_row(s: Session) -> dict[str, Any]:
        return {
            "id": s.id,
            "user_id": s.user_id,
            "kind": s.kind.value,
            "created_at": s.created_at,
            "last_seen_at": s.last_seen_at,
            "revoked_at": s.revoked_at,
            "revoke_reason": s.revoke_reason,
            "ip_prefix": s.ip_prefix,
        }

    @staticmethod
    def _session(row: Row[Any]) -> Session:
        return Session(
            id=row.id,
            user_id=row.user_id,
            kind=SessionKind(row.kind),
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
            revoked_at=row.revoked_at,
            revoke_reason=row.revoke_reason,
            ip_prefix=row.ip_prefix,
        )

    @staticmethod
    def _token_row(t: RefreshToken) -> dict[str, Any]:
        return {
            "id": t.id,
            "session_id": t.session_id,
            "token_hash": t.token_hash,
            "parent_id": t.parent_id,
            "created_at": t.created_at,
            "expires_at": t.expires_at,
            "used_at": t.used_at,
        }


class SqlAuthIdentityRepository:
    """Привязанные внешние аккаунты."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, provider: Provider, subject: str) -> AuthIdentity | None:
        """По аккаунту провайдера."""
        return await self._one(
            and_(auth_identities.c.provider == provider.value, auth_identities.c.subject == subject)
        )

    async def get_for_user(self, user_id: UUID, provider: Provider) -> AuthIdentity | None:
        """Привязка провайдера у пользователя."""
        return await self._one(
            and_(
                auth_identities.c.user_id == user_id,
                auth_identities.c.provider == provider.value,
            )
        )

    async def list_for_user(self, user_id: UUID) -> list[AuthIdentity]:
        """Все привязки."""
        rows = await self._s.execute(
            select(auth_identities)
            .where(auth_identities.c.user_id == user_id)
            .order_by(auth_identities.c.provider)
        )
        return [self._identity(r) for r in rows]

    async def add(self, identity: AuthIdentity) -> None:
        """Привязать; гонка за тот же аккаунт провайдера — ``ConflictError``."""
        try:
            async with self._s.begin_nested():
                await self._s.execute(
                    insert(auth_identities).values(
                        user_id=identity.user_id,
                        provider=identity.provider.value,
                        subject=identity.subject,
                        display=identity.display,
                        linked_at=identity.linked_at,
                    )
                )
        except IntegrityError as exc:
            code = (
                "auth.provider_already_linked"
                if "user_id" in _constraint(exc)
                else "auth.provider_account_taken"
            )
            raise ConflictError("аккаунт уже привязан", code=code) from exc

    async def remove(self, user_id: UUID, provider: Provider) -> bool:
        """Отвязать."""
        result = await self._s.execute(
            delete(auth_identities).where(
                auth_identities.c.user_id == user_id,
                auth_identities.c.provider == provider.value,
            )
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]  # CursorResult у DELETE

    async def _one(self, condition: Any) -> AuthIdentity | None:
        row = (await self._s.execute(select(auth_identities).where(condition))).first()
        return self._identity(row) if row else None

    @staticmethod
    def _identity(row: Row[Any]) -> AuthIdentity:
        return AuthIdentity(
            user_id=row.user_id,
            provider=Provider(row.provider),
            subject=row.subject,
            display=row.display,
            linked_at=row.linked_at,
        )


class SqlRoleRepository:
    """Матрица ролей и аудит."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_matrix(self) -> RoleMatrix:
        """Текущая матрица."""
        perms = frozenset((await self._s.scalars(select(permissions.c.name))).all())
        names = (await self._s.scalars(select(roles.c.name))).all()
        mapping: dict[str, set[str]] = {name: set() for name in names}
        for role, perm in await self._s.execute(select(role_permissions)):
            mapping.setdefault(role, set()).add(perm)
        return RoleMatrix(roles={k: frozenset(v) for k, v in mapping.items()}, permissions=perms)

    async def save_matrix(self, matrix: RoleMatrix) -> None:
        """Заменить роли и связи с правами."""
        names = sorted(matrix.roles)
        await self._s.execute(
            pg_insert(roles).values([{"name": n} for n in names]).on_conflict_do_nothing()
        )
        await self._s.execute(delete(role_permissions))
        await self._s.execute(delete(roles).where(roles.c.name.not_in(names)))
        pairs = [{"role": r, "perm": p} for r in names for p in sorted(matrix.roles[r])]
        if pairs:
            await self._s.execute(insert(role_permissions), pairs)

    async def add_audit(self, change: RoleChange) -> None:
        """Запись аудита."""
        await self._s.execute(
            insert(role_changes).values(
                id=change.id,
                kind=change.kind,
                user_id=change.user_id,
                actor_id=change.actor_id,
                before=dict(change.before),
                after=dict(change.after),
                reason=change.reason,
                created_at=change.created_at,
            )
        )


class SqlConsentRepository:
    """Согласия."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, consents: Iterable[Consent]) -> None:
        """Добавить, пропуская уже выданные."""
        rows = [
            {"user_id": c.user_id, "kind": c.kind, "version": c.version, "granted_at": c.granted_at}
            for c in consents
        ]
        if rows:
            await self._s.execute(pg_insert(consents_table).values(rows).on_conflict_do_nothing())

    async def list_for_user(self, user_id: UUID) -> list[Consent]:
        """Согласия пользователя."""
        rows = await self._s.execute(
            select(consents_table).where(consents_table.c.user_id == user_id)
        )
        return [Consent(r.user_id, r.kind, r.version, r.granted_at) for r in rows]


class SqlEmailTokenRepository:
    """Одноразовые токены из писем."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, token: EmailToken) -> None:
        """Сохранить."""
        await self._s.execute(
            insert(email_tokens).values(
                id=token.id,
                user_id=token.user_id,
                purpose=token.purpose.value,
                token_hash=token.token_hash,
                created_at=token.created_at,
                expires_at=token.expires_at,
            )
        )

    async def consume(
        self, token_hash: str, purpose: EmailTokenPurpose, now: datetime
    ) -> UUID | None:
        """Погасить атомарно одним UPDATE (повтор и гонка невозможны)."""
        user_id: UUID | None = await self._s.scalar(
            update(email_tokens)
            .where(
                email_tokens.c.token_hash == token_hash,
                email_tokens.c.purpose == purpose.value,
                email_tokens.c.used_at.is_(None),
                email_tokens.c.expires_at > now,
            )
            .values(used_at=now)
            .returning(email_tokens.c.user_id)
        )
        return user_id

    async def invalidate_for_user(
        self, user_id: UUID, purpose: EmailTokenPurpose, now: datetime
    ) -> int:
        """Погасить все непогашенные токены пользователя этой цели одним UPDATE."""
        result = await self._s.execute(
            update(email_tokens)
            .where(
                email_tokens.c.user_id == user_id,
                email_tokens.c.purpose == purpose.value,
                email_tokens.c.used_at.is_(None),
            )
            .values(used_at=now)
        )
        return int(result.rowcount or 0)  # type: ignore[attr-defined]  # CursorResult у UPDATE


class SqlIdentityUnitOfWork:
    """Unit of Work identity: транзакция + outbox (gv_db) и репозитории сервиса."""

    def __init__(
        self, sessions_factory: async_sessionmaker[AsyncSession], *, producer: str, clock: Clock
    ) -> None:
        self._inner = SqlAlchemyUnitOfWork(
            sessions_factory, OutboxWriter(TABLES.outbox), producer=producer, clock=clock
        )
        self._clock = clock

    async def __aenter__(self) -> Self:
        await self._inner.__aenter__()
        s = self._inner.session
        self.users = SqlUserRepository(s)
        self.credentials = SqlCredentialRepository(s)
        self.sessions = SqlSessionRepository(s)
        self.identities = SqlAuthIdentityRepository(s)
        self.roles = SqlRoleRepository(s)
        self.consents = SqlConsentRepository(s)
        self.email_tokens = SqlEmailTokenRepository(s)
        self.idempotency = SqlIdempotencyStore(s, idempotency_keys, self._clock)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._inner.__aexit__(exc_type, exc, tb)

    def track(self, aggregate: AggregateRoot[Any]) -> None:
        """Отслеживать агрегат (события — в outbox при коммите)."""
        self._inner.track(aggregate)

    async def commit(self) -> None:
        """Закоммитить данные и события."""
        await self._inner.commit()

    async def rollback(self) -> None:
        """Откатить."""
        await self._inner.rollback()
