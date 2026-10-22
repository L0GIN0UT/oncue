"""Общая часть сценариев с одноразовым кодом из письма (ADR 0041).

Инварианты:

* попытка списывается ДО сравнения и в отдельной транзакции (``ChallengeAttempts.spend``):
  откат запроса не возвращает попытку;
* погашение — условный UPDATE по результату (``consume``), ``False`` — ошибка;
* один активный код на (субъект, назначение): перед созданием прежний отменяется;
* код и его хэш не попадают в логи.
"""

from __future__ import annotations

import asyncio
import hmac
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from gv_core.clock import Clock
from gv_core.errors import GoneError, InvalidInputError, RateLimitedError
from gv_core.ids import uuid7
from identity_service.application.config import AuthConfig
from identity_service.application.dto import ChallengeIssued
from identity_service.application.ports import (
    ChallengeAttempts,
    ChallengePurpose,
    CodeService,
    EmailChallenge,
    IdentityUnitOfWork,
    RateLimiter,
    SecretGenerator,
    TrustedDevice,
)
from identity_service.application.security import (
    decoy_subject_hash,
    ensure_allowed,
    hash_secret,
    mail_key,
    rate_key,
)
from identity_service.application.sessions import IssuedSession

DEVICE_TOKEN_BYTES = 32
_DAY = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class VerifiedSession:
    """Сессия, выданная после ввода кода, и токен доверенного устройства для cookie.

    Attributes:
        session: Выданная сессия.
        device_token: Значение cookie ``gv_device`` (всегда новое).
    """

    session: IssuedSession
    device_token: str


def device_hash(device_token: str | None) -> str | None:
    """sha256 cookie устройства (``None`` — cookie нет)."""
    return hash_secret(device_token) if device_token else None


def code_invalid(attempts_left: int) -> InvalidInputError:
    """Неверный (или неактивный) код."""
    return InvalidInputError(
        "неверный код", code="auth.code_invalid", details={"attempts_left": attempts_left}
    )


class CodeChallenges:
    """Выдача, проверка и погашение кодов; используется всеми сценариями с кодом."""

    def __init__(
        self,
        *,
        codes: CodeService,
        attempts: ChallengeAttempts,
        limiter: RateLimiter,
        secrets: SecretGenerator,
        clock: Clock,
        config: AuthConfig,
    ) -> None:
        self._codes, self._attempts, self._limiter = codes, attempts, limiter
        self._secrets, self._clock, self._config = secrets, clock, config
        # Проверка держит два соединения БД (основное и для списания попытки): без
        # ограничения параллельные вводы кодов исчерпали бы пул и остановили весь сервис.
        self._check_slots = asyncio.Semaphore(config.codes.check_concurrency)

    # ------------------------------------------------------------------ лимиты

    async def limit_requests(self, ip: str | None) -> None:
        """Лимит запросов кода с IP (``request_per_ip_per_min``)."""
        if ip:
            ensure_allowed(
                await self._limiter.hit(
                    rate_key("code:request:ip", ip),
                    limit=self._config.codes.request_per_ip_per_min,
                    window_s=60,
                )
            )

    async def limit_confirms(self, ip: str | None) -> None:
        """Лимит вводов кода с IP (``confirm_per_ip_per_min``)."""
        if ip:
            ensure_allowed(
                await self._limiter.hit(
                    rate_key("code:confirm:ip", ip),
                    limit=self._config.codes.confirm_per_ip_per_min,
                    window_s=60,
                )
            )

    # ------------------------------------------------------------------ выдача

    def new_binding(self) -> tuple[str, str]:
        """Одноразовый токен привязки к браузеру (cookie ``gv_bind``) и его sha256."""
        token = self._secrets.token(DEVICE_TOKEN_BYTES)
        return token, hash_secret(token)

    def require_binding(self, challenge: EmailChallenge, binding_token: str | None) -> None:
        """Код привязан к браузеру: из другого он считается неверным (попытка уже списана).

        Raises:
            InvalidInputError: ``auth.code_invalid`` — привязка есть, а cookie нет или чужое.
        """
        expected = challenge.device_hash
        if expected is None:
            return
        given = hash_secret(binding_token) if binding_token else ""
        if not hmac.compare_digest(given.encode(), expected.encode()):
            raise code_invalid(challenge.attempts_left)

    def view(
        self, challenge: EmailChallenge, *, binding_token: str | None = None
    ) -> ChallengeIssued:
        """Ответ клиенту по записи."""
        cfg = self._config.codes
        return ChallengeIssued(
            challenge_id=challenge.id,
            purpose=challenge.purpose.value,
            code_length=cfg.length,
            expires_in_s=challenge.expires_in_s(self._clock.now()),
            resend_after_s=cfg.resend_after_s,
            binding_token=binding_token,
        )

    def fake(
        self, purpose: ChallengePurpose, *, binding_token: str | None = None
    ) -> ChallengeIssued:
        """Ответ-«пустышка» без записи (письмо подавлено лимитом): та же форма, фиктивный id."""
        cfg = self._config.codes
        return ChallengeIssued(
            uuid7(), purpose.value, cfg.length, cfg.ttl_s, cfg.resend_after_s, binding_token
        )

    async def issue(
        self,
        uow: IdentityUnitOfWork,
        *,
        purpose: ChallengePurpose,
        subject_hash: str,
        user_id: UUID | None,
        device_hash: str | None = None,
        mail_key: str | None = None,
        session_id: UUID | None = None,
    ) -> tuple[EmailChallenge, str]:
        """Создать код, отменив прежний активный (субъект, назначение).

        Returns:
            Запись и сам код (его нужно только положить в письмо).
        """
        code = self._codes.generate(self._config.codes.length)
        challenge = await self._store(
            uow,
            purpose=purpose,
            subject_hash=subject_hash,
            user_id=user_id,
            device_hash=device_hash,
            mail_key=mail_key,
            session_id=session_id,
            code=code,
        )
        return challenge, code

    async def issue_decoy(
        self,
        uow: IdentityUnitOfWork,
        purpose: ChallengePurpose,
        address: str,
        *,
        binding: tuple[str, str] | None = None,
    ) -> ChallengeIssued:
        """«Пустышка» для занятого/несуществующего адреса: запись есть, кода никто не знает.

        Попытки, срок, замена прежней записи и суточный потолок ведут себя как у настоящего
        кода, поэтому адрес с аккаунтом нельзя отличить. Субъект пустышки (``decoy_subject_hash``)
        отдельный от субъекта настоящего кода: пустышка не отменяет чужой настоящий код.
        """
        challenge_id = uuid7()
        challenge = await self._store(
            uow,
            purpose=purpose,
            subject_hash=decoy_subject_hash(address),
            user_id=None,
            device_hash=binding[1] if binding else None,
            mail_key=mail_key(address),
            session_id=None,
            code=self._codes.generate(self._config.codes.length),
            challenge_id=challenge_id,
        )
        return self.view(challenge, binding_token=binding[0] if binding else None)

    async def _store(
        self,
        uow: IdentityUnitOfWork,
        *,
        purpose: ChallengePurpose,
        subject_hash: str,
        user_id: UUID | None,
        device_hash: str | None,
        mail_key: str | None,
        session_id: UUID | None,
        code: str,
        challenge_id: UUID | None = None,
    ) -> EmailChallenge:
        now = self._clock.now()
        cfg = self._config.codes
        cid = challenge_id or uuid7()
        await uow.email_challenges.cancel_active(subject_hash, purpose, now)
        challenge = EmailChallenge(
            id=cid,
            purpose=purpose,
            subject_hash=subject_hash,
            user_id=user_id,
            code_hash=self._codes.digest(challenge_id=cid, code=code),
            attempts=0,
            max_attempts=cfg.max_attempts,
            send_count=1,
            device_hash=device_hash,
            mail_key=mail_key,
            session_id=session_id,
            created_at=now,
            expires_at=now + timedelta(seconds=cfg.ttl_s),
            last_sent_at=now,
        )
        await uow.email_challenges.add(challenge)
        return challenge

    # ------------------------------------------------------------------ проверка

    async def check(
        self,
        uow: IdentityUnitOfWork,
        challenge_id: UUID,
        code: str,
        purpose: ChallengePurpose,
        ip: str | None,
    ) -> EmailChallenge:
        """Проверить код: лимиты, суточный потолок, списание попытки, сравнение.

        Погашение — отдельным шагом ``consume`` в транзакции вызывающего.

        Raises:
            RateLimitedError: ``rate.limited`` (IP) или ``auth.code_limit_exceeded`` (сутки).
            InvalidInputError: ``auth.code_invalid`` (``attempts_left``).
            GoneError: ``auth.code_expired``.
        """
        await self.limit_confirms(ip)
        async with self._check_slots:
            return await self._check(uow, challenge_id, code, purpose)

    async def _check(
        self, uow: IdentityUnitOfWork, challenge_id: UUID, code: str, purpose: ChallengePurpose
    ) -> EmailChallenge:
        now = self._clock.now()
        known = await uow.email_challenges.get(challenge_id)
        if known is None or known.purpose is not purpose:
            raise code_invalid(0)
        failures = await uow.email_challenges.failures_since(known.subject_hash, now - _DAY)
        if failures >= self._config.codes.daily_fail_limit:
            raise RateLimitedError(
                "слишком много неверных кодов, повторите завтра",
                code="auth.code_limit_exceeded",
                details={"retry_after": int(_DAY.total_seconds())},
            )
        spent = await self._attempts.spend(challenge_id, now)
        if spent is None:
            if known.is_expired(now):
                raise GoneError("код истёк", code="auth.code_expired")
            raise code_invalid(0)
        if not self._codes.matches(spent.code_hash, challenge_id=challenge_id, code=code):
            raise code_invalid(spent.attempts_left)
        return spent

    async def consume(self, uow: IdentityUnitOfWork, challenge: EmailChallenge) -> None:
        """Погасить проверенный код (условный UPDATE).

        Raises:
            InvalidInputError: ``auth.code_invalid`` — код уже погашен/отменён/истёк (гонка).
        """
        if not await uow.email_challenges.consume(challenge.id, self._clock.now()):
            raise code_invalid(0)

    # ------------------------------------------------------------------ устройства

    async def trust_device(self, uow: IdentityUnitOfWork, user_id: UUID) -> str:
        """Доверить устройство; возвращает НОВОЕ значение cookie.

        Пришедший ``gv_device`` не принимается: подсаженный атакующим токен (cookie без
        ``__Host-``) не стал бы доверенным после того, как жертва введёт свой код.
        """
        now = self._clock.now()
        token = self._secrets.token(DEVICE_TOKEN_BYTES)
        await uow.trusted_devices.add(
            TrustedDevice(
                id=uuid7(),
                user_id=user_id,
                device_hash=hash_secret(token),
                created_at=now,
                expires_at=now + timedelta(seconds=self._config.codes.device_ttl_s),
            )
        )
        return token

    async def is_trusted(
        self, uow: IdentityUnitOfWork, user_id: UUID, device_token: str | None
    ) -> bool:
        """Устройство доверено пользователю."""
        if not device_token:
            return False
        return await uow.trusted_devices.is_trusted(
            user_id, hash_secret(device_token), self._clock.now()
        )

    # ------------------------------------------------------------------ повтор

    async def resend(
        self,
        uow: IdentityUnitOfWork,
        challenge: EmailChallenge,
        *,
        send_allowed: Callable[[EmailChallenge], Awaitable[bool]],
    ) -> tuple[EmailChallenge, str | None]:
        """Новый код для существующей записи (или новая запись, если прежняя истекла).

        ``send_allowed`` спрашивает лимит писем на адрес. Он расходуется одинаково у настоящего
        кода и у пустышки, а при отказе запись остаётся как есть (код ``None``): иначе
        исчерпанный лимит оставлял бы владельца с заменённым, но не отправленным кодом.

        Returns:
            Запись и новый код; код ``None`` — письмо не разрешено, ничего не изменено.

        Raises:
            InvalidInputError: ``auth.code_invalid`` — код погашен или отменён.
            RateLimitedError: ``auth.code_resend_too_early`` (``retry_after``).
        """
        now = self._clock.now()
        if challenge.consumed_at is not None or challenge.cancelled_at is not None:
            raise code_invalid(0)
        age = now - challenge.created_at
        replace_record = challenge.is_expired(now) or age >= timedelta(
            seconds=self._config.codes.max_lifetime_s
        )
        if not replace_record:
            wait = challenge.resend_wait_s(now, self._config.codes.resend_after_s)
            if wait > 0:
                raise RateLimitedError(
                    "повторная отправка пока недоступна",
                    code="auth.code_resend_too_early",
                    details={"retry_after": wait},
                )
        if not await send_allowed(challenge):
            return challenge, None
        if replace_record:
            # Запись старше абсолютного срока заменяется новой (свежий created_at): resend не
            # может держать одну запись живой, пока она не выпадет из окна суточного потолка.
            return await self.issue(
                uow,
                purpose=challenge.purpose,
                subject_hash=challenge.subject_hash,
                user_id=challenge.user_id,
                device_hash=challenge.device_hash,
                mail_key=challenge.mail_key,
                session_id=challenge.session_id,
            )
        code = self._codes.generate(self._config.codes.length)
        replaced = await uow.email_challenges.replace_code(
            challenge.id,
            code_hash=self._codes.digest(challenge_id=challenge.id, code=code),
            expires_at=now + timedelta(seconds=self._config.codes.ttl_s),
            now=now,
        )
        updated = await uow.email_challenges.get(challenge.id) if replaced else None
        if updated is None:
            raise code_invalid(0)
        return updated, code
