"""Порты identity: что application требует от инфраструктуры.

Реализации — ``identity_service.infrastructure``; фейки для unit-тестов — ``tests/fakes.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from gv_core.uow import UnitOfWork
from gv_db.idempotency import IdempotencyStore
from identity_service.domain import (
    AuthIdentity,
    ChallengePurpose,
    Consent,
    EmailAddress,
    EmailChallenge,
    Nickname,
    Provider,
    RefreshToken,
    RoleMatrix,
    Session,
    TrustedDevice,
    User,
)

__all__ = ["ChallengePurpose", "EmailChallenge", "TrustedDevice"]  # реэкспорт доменных типов

# ----------------------------------------------------------------------------- DTO


@dataclass(frozen=True, slots=True)
class RoleChange:
    """Запись аудита изменения ролей пользователя или матрицы (append-only)."""

    id: UUID
    kind: str  # "user_roles" | "matrix"
    user_id: UUID | None
    actor_id: UUID | None
    before: Mapping[str, Any]
    after: Mapping[str, Any]
    reason: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class RateDecision:
    """Решение лимитера."""

    allowed: bool
    retry_after_s: int = 0


@dataclass(frozen=True, slots=True)
class IssuedAccess:
    """Подписанный access-токен."""

    token: str
    expires_in: int


@dataclass(frozen=True, slots=True)
class OAuthState:
    """Состояние незавершённого OAuth-входа (одноразовое, TTL 10 минут).

    Attributes:
        purpose: ``link`` — привязка к вошедшему пользователю, ``login`` — вход.
        user_id: Для ``link``: кто начал привязку.
        session_id: Для ``link``: сессия, в которой начали (``state`` связан с ``sid``).
        code_verifier: PKCE verifier (RFC 7636).
        browser_binding: SHA-256 от nonce из cookie браузера, начавшего вход.
        return_to: Относительный путь возврата из allowlist.
    """

    purpose: str
    provider: Provider
    user_id: UUID | None
    session_id: UUID | None
    code_verifier: str
    browser_binding: str
    return_to: str


@dataclass(frozen=True, slots=True)
class ExternalProfile:
    """Профиль у провайдера: только идентификатор и отображаемое имя (без e-mail)."""

    subject: str
    display: str | None


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """Письмо. ``link`` содержит секрет — его нельзя логировать в production.

    Attributes:
        purpose: ``verify`` | ``reset`` | ``already_registered`` | ``provider_linked`` |
            ``provider_unlinked`` | ``password_changed``.
        providers: Внешние сервисы, о которых сообщает письмо (привязка, отвязка, список
            привязок при смене пароля).
    """

    to: EmailAddress
    purpose: str
    link: str | None
    providers: tuple[str, ...] = ()
    code: str | None = None
    """Одноразовый код (ADR 0041); как и ``link``, секрет: в лог только local/ci."""
    challenge_id: UUID | None = None
    """Код, к которому относится письмо (e2e и аудит находят код в логе по нему)."""


# ----------------------------------------------------------------------------- репозитории


class UserRepository(Protocol):
    """Хранилище пользователей (с ролями)."""

    async def add(self, user: User) -> None:
        """Вставить. ``ConflictError``: ``user.nickname_taken`` / ``user.email_taken``."""
        ...

    async def get(self, user_id: UUID) -> User | None:
        """По идентификатору (в том числе удалённого)."""
        ...

    async def find_by_email(self, email: EmailAddress) -> User | None:
        """По e-mail (без учёта регистра)."""
        ...

    async def find_by_nickname(self, nickname: Nickname) -> User | None:
        """По никнейму (без учёта регистра)."""
        ...

    async def save(self, user: User) -> None:
        """Сохранить изменения. ``ConflictError`` при гонке (оптимистичная блокировка)."""
        ...

    async def search(self, query: str | None, *, after: UUID | None, limit: int) -> list[User]:
        """Страница пользователей по возрастанию id; ``query`` — префикс ника или e-mail."""
        ...

    async def ids_with_roles(self, roles: Iterable[str]) -> list[UUID]:
        """Активные пользователи, у которых есть хотя бы одна из ролей."""
        ...


class CredentialRepository(Protocol):
    """Хэши паролей."""

    async def get_password_hash(self, user_id: UUID) -> str | None:
        """Хэш или ``None``."""
        ...

    async def set_password_hash(self, user_id: UUID, password_hash: str, now: datetime) -> None:
        """Создать или заменить."""
        ...

    async def delete(self, user_id: UUID) -> None:
        """Удалить учётные данные."""
        ...


class SessionRepository(Protocol):
    """Сессии и refresh-токены."""

    async def add(self, session: Session) -> None:
        """Новая сессия."""
        ...

    async def get(self, session_id: UUID) -> Session | None:
        """Сессия по ``sid``."""
        ...

    async def save(self, session: Session) -> None:
        """Сохранить изменения сессии."""
        ...

    async def add_refresh(self, token: RefreshToken) -> None:
        """Новый refresh-токен."""
        ...

    async def find_refresh_for_update(self, token_hash: str) -> RefreshToken | None:
        """Токен по хэшу с блокировкой строки: параллельные ротации сериализуются."""
        ...

    async def save_refresh(self, token: RefreshToken) -> None:
        """Сохранить изменения токена."""
        ...

    async def active_for_user(self, user_id: UUID) -> list[Session]:
        """Неотозванные сессии пользователя."""
        ...


class AuthIdentityRepository(Protocol):
    """Привязанные внешние аккаунты."""

    async def get(self, provider: Provider, subject: str) -> AuthIdentity | None:
        """По аккаунту провайдера."""
        ...

    async def get_for_user(self, user_id: UUID, provider: Provider) -> AuthIdentity | None:
        """Привязка провайдера у пользователя."""
        ...

    async def list_for_user(self, user_id: UUID) -> list[AuthIdentity]:
        """Все привязки пользователя."""
        ...

    async def add(self, identity: AuthIdentity) -> None:
        """Привязать. ``ConflictError`` ``auth.provider_account_taken`` при гонке."""
        ...

    async def remove(self, user_id: UUID, provider: Provider) -> bool:
        """Отвязать; ``False`` — привязки не было."""
        ...


class RoleRepository(Protocol):
    """Матрица ролей и аудит."""

    async def get_matrix(self) -> RoleMatrix:
        """Текущая матрица."""
        ...

    async def save_matrix(self, matrix: RoleMatrix) -> None:
        """Заменить матрицу (роли и их права)."""
        ...

    async def add_audit(self, change: RoleChange) -> None:
        """Дописать запись аудита."""
        ...


class ConsentRepository(Protocol):
    """Согласия (append-only)."""

    async def add(self, consents: Iterable[Consent]) -> None:
        """Добавить; уже выданные (kind+version) пропускаются."""
        ...

    async def list_for_user(self, user_id: UUID) -> list[Consent]:
        """Согласия пользователя."""
        ...


class EmailChallengeRepository(Protocol):
    """Коды из писем (в транзакции use-case)."""

    async def add(self, challenge: EmailChallenge) -> None:
        """Сохранить новый код; прежний активный (субъект, назначение) гасится вызывающим."""
        ...

    async def get(self, challenge_id: UUID) -> EmailChallenge | None:
        """Код по id."""
        ...

    async def cancel_active(
        self, subject_hash: str, purpose: ChallengePurpose, now: datetime
    ) -> int:
        """Отменить действующий код (субъект, назначение); вернуть, сколько отменено."""
        ...

    async def replace_code(
        self, challenge_id: UUID, *, code_hash: str, expires_at: datetime, now: datetime
    ) -> bool:
        """Повторная отправка: новый код в той же записи, ``send_count + 1``, попытки сброшены.

        Условный UPDATE: ``False``, если код погашен, отменён, истёк или ``last_sent_at``
        ближе ``resend_after_s`` (проверку времени задаёт вызывающий через ``now``).
        """
        ...

    async def consume(self, challenge_id: UUID, now: datetime) -> bool:
        """Погасить действующий код условным UPDATE; ``False`` — уже погашен/отменён/истёк."""
        ...

    async def delete_for_subjects(self, user_id: UUID, subject_hashes: Iterable[str]) -> int:
        """Удалить все коды пользователя и коды с этими субъектами (удаление аккаунта)."""
        ...

    async def failures_since(self, subject_hash: str, since: datetime) -> int:
        """Неудачные вводы по субъекту за период (дневной потолок)."""
        ...


class ChallengeAttempts(Protocol):
    """Списание попытки в СВОЕЙ транзакции, независимой от транзакции use-case.

    Иначе откат запроса (ошибка пароля, сбой после проверки) возвращал бы попытку и давал
    бесконечный перебор кода (ADR 0041).
    """

    async def spend(self, challenge_id: UUID, now: datetime) -> EmailChallenge | None:
        """Атомарно ``attempts + 1`` у действующего кода с ``attempts < max_attempts``.

        Returns:
            Запись после списания или ``None`` — кода нет, он неактивен или попытки исчерпаны.
        """
        ...


class TrustedDeviceRepository(Protocol):
    """Доверенные устройства."""

    async def add(self, device: TrustedDevice) -> None:
        """Доверить устройство (повтор для той же пары продлевает срок)."""
        ...

    async def is_trusted(self, user_id: UUID, device_hash: str, now: datetime) -> bool:
        """Устройство доверено, не отозвано и не истекло."""
        ...

    async def revoke_for_user(
        self, user_id: UUID, now: datetime, *, except_hash: str | None = None
    ) -> int:
        """Снять доверие со всех устройств пользователя, кроме ``except_hash``."""
        ...

    async def delete_for_user(self, user_id: UUID) -> int:
        """Удалить все устройства пользователя (удаление аккаунта)."""
        ...


class IdentityUnitOfWork(UnitOfWork, Protocol):
    """Транзакция identity с репозиториями, привязанными к ней."""

    @property
    def users(self) -> UserRepository:
        """Репозиторий в транзакции."""
        ...

    @property
    def credentials(self) -> CredentialRepository:
        """Репозиторий в транзакции."""
        ...

    @property
    def sessions(self) -> SessionRepository:
        """Репозиторий в транзакции."""
        ...

    @property
    def identities(self) -> AuthIdentityRepository:
        """Репозиторий в транзакции."""
        ...

    @property
    def roles(self) -> RoleRepository:
        """Репозиторий в транзакции."""
        ...

    @property
    def consents(self) -> ConsentRepository:
        """Репозиторий в транзакции."""
        ...

    @property
    def email_challenges(self) -> EmailChallengeRepository:
        """Репозиторий в транзакции."""
        ...

    @property
    def trusted_devices(self) -> TrustedDeviceRepository:
        """Репозиторий в транзакции."""
        ...

    @property
    def idempotency(self) -> IdempotencyStore:
        """Ключи идемпотентности admin-мутаций в этой же транзакции (gv_db.idempotency)."""
        ...


UnitOfWorkFactory = Callable[[], IdentityUnitOfWork]


# ----------------------------------------------------------------------------- безопасность


class PasswordHasher(Protocol):
    """Хэширование паролей (argon2id)."""

    async def hash(self, password: str) -> str:
        """Новый хэш."""
        ...

    async def verify(self, password_hash: str, password: str) -> bool:
        """Совпадает ли пароль."""
        ...

    def needs_rehash(self, password_hash: str) -> bool:
        """Параметры хэша устарели."""
        ...

    async def verify_dummy(self, password: str) -> None:
        """Проверка против фиктивного хэша: то же время, что и для существующего аккаунта."""
        ...


class TokenIssuer(Protocol):
    """Выпуск access JWT."""

    def issue_access(
        self, *, user_id: UUID, session_id: UUID, roles: Iterable[str], perms: Iterable[str]
    ) -> IssuedAccess:
        """Подписать токен активным ключом."""
        ...


class GuestTokenIssuer(Protocol):
    """Выпуск access JWT гостя (D5 плана этапа 2)."""

    def issue_guest_access(self, *, guest_id: UUID, session_id: UUID) -> IssuedAccess:
        """Токен ``sub = guest_id``, ``roles = ["guest"]``, ``perms = []``, ``gst = true``."""
        ...


class GuestSessionPurger(Protocol):
    """Удаление заброшенных гостевых сессий (вместе с их refresh-токенами)."""

    async def purge(self, *, idle_before: datetime, revoked_before: datetime, limit: int) -> int:
        """Удалить до ``limit`` гостевых сессий: ``last_seen_at <= idle_before`` или
        ``revoked_at <= revoked_before`` (границы — как в ``domain.guest.is_abandoned``).
        Возвращает число удалённых."""
        ...


class ChallengePurger(Protocol):
    """Удаление просроченных кодов и отозванных/истёкших доверенных устройств."""

    async def purge(
        self,
        *,
        challenges_before: datetime,
        devices_revoked_before: datetime,
        now: datetime,
        limit: int,
    ) -> int:
        """Удалить до ``limit`` строк каждой таблицы: коды с ``expires_at <= challenges_before``,
        устройства с ``expires_at <= now`` или ``revoked_at <= devices_revoked_before``.
        Возвращает общее число удалённых строк."""
        ...


class SigningKeyProvider(Protocol):
    """Публичные ключи подписи."""

    def jwks(self) -> dict[str, Any]:
        """JWKS со всеми неистёкшими ключами."""
        ...


class SecretGenerator(Protocol):
    """Источник криптостойкой случайности."""

    def token(self, nbytes: int) -> str:
        """Случайная строка base64url из ``nbytes`` байт."""
        ...


class CodeService(Protocol):
    """Генерация и проверка одноразовых кодов (``secrets.randbelow``, HMAC)."""

    def generate(self, length: int) -> str:
        """Случайный числовой код ровно из ``length`` цифр (с ведущими нулями)."""
        ...

    def digest(self, *, challenge_id: UUID, code: str) -> str:
        """HMAC кода, привязанный к ``challenge_id`` (код нельзя перенести на другую запись)."""
        ...

    def matches(self, stored: str, *, challenge_id: UUID, code: str) -> bool:
        """Сравнение за постоянное время (``hmac.compare_digest``)."""
        ...


class RateLimiter(Protocol):
    """Лимитер частоты (фиксированное окно).

    Попытка засчитывается атомарно (``hit``) ДО проверки — параллельные запросы не могут
    проскочить между «проверить» и «засчитать». Успешную попытку вызывающий прощает
    (``forgive``) или обнуляет счётчик (``reset``).
    """

    async def hit(self, key: str, *, limit: int, window_s: int) -> RateDecision:
        """Засчитать попытку и решить, разрешена ли она."""
        ...

    async def forgive(self, key: str) -> None:
        """Вернуть одну засчитанную попытку (счётчик не уходит ниже нуля)."""
        ...

    async def reset(self, key: str) -> None:
        """Обнулить счётчик."""
        ...


class RevocationStore(Protocol):
    """Denylist ``sid`` для уже выданных access-токенов."""

    async def revoke(self, session_ids: Iterable[UUID]) -> None:
        """Отозвать сессии на время жизни access-токена."""
        ...


class OAuthStateStore(Protocol):
    """Одноразовые состояния OAuth."""

    async def put(self, key: str, state: OAuthState, ttl_s: int) -> None:
        """Сохранить."""
        ...

    async def pop(self, key: str) -> OAuthState | None:
        """Забрать и удалить атомарно (повторное использование невозможно)."""
        ...


class OAuthProvider(Protocol):
    """Внешний OAuth-провайдер (authorization code + PKCE S256)."""

    provider: Provider

    def authorize_url(self, *, state: str, code_challenge: str) -> str:
        """URL страницы согласия; ``redirect_uri`` — строго из конфигурации."""
        ...

    async def exchange_code(self, *, code: str, code_verifier: str) -> str:
        """Обменять код на access-токен провайдера (не сохраняется)."""
        ...

    async def fetch_profile(self, access_token: str) -> ExternalProfile:
        """Идентификатор пользователя у провайдера."""
        ...


class EmailSender(Protocol):
    """Отправка писем."""

    async def send(self, message: EmailMessage) -> None:
        """Отправить письмо."""
        ...
