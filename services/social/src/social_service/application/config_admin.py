"""Версии конфига social: чтение, черновик, публикация, откат (ADR 0053).

Слой для admin-API (поток F4): ручки ``/admin/v1/config/*`` только вызывают эти use-cases.
Аудит (``admin_actions``) пишет F4 через ``on_change`` в той же транзакции. Публикация
привязана к содержимому: нужен ``body_sha256`` черновика, который видел админ.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from gv_core.clock import Clock
from gv_core.errors import ConflictError, NotFoundError
from gv_db.idempotency import request_fingerprint, run_idempotent
from social_service.application.ports.config_store import (
    ConfigUnitOfWork,
    ConfigUowFactory,
    ConfigVersionRow,
)

type Json = dict[str, Any]
type Validator = Callable[[Mapping[str, Any]], None]


class ConfigVersionNotFoundError(NotFoundError):
    """Нет такой версии конфига."""

    default_code = "social.config_version_not_found"


class ConfigNotDraftError(ConflictError):
    """Публиковать можно только черновик."""

    default_code = "social.config_not_draft"


class DraftChangedError(ConflictError):
    """Черновик изменился после просмотра (не совпал ``body_sha256``)."""

    default_code = "social.config_draft_changed"


class ConfigNeverPublishedError(ConflictError):
    """Откат возможен только к версии, которая когда-то действовала."""

    default_code = "social.config_never_published"


@dataclass(frozen=True, slots=True)
class ConfigChange:
    """Описание изменения для аудита (передаётся в ``on_change``)."""

    action: str  # config.save_draft | config.publish | config.rollback
    version: int
    actor_id: UUID
    reason: str
    details: Json


type ChangeHook = Callable[[ConfigUnitOfWork, ConfigChange], Awaitable[None]]


def body_sha256(body: Mapping[str, Any]) -> str:
    """sha256 канонического JSON тела."""
    raw = json.dumps(dict(body), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def card(row: ConfigVersionRow) -> Json:
    """Карточка версии (без тела)."""
    return {
        "version": row.version,
        "status": row.status,
        "created_at": row.created_at.isoformat(),
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "created_by": str(row.created_by),
        "body_sha256": row.body_sha256,
    }


def with_body(row: ConfigVersionRow) -> Json:
    """Версия с телом."""
    return {"card": card(row), "config": row.body}


class ConfigQueries:
    """Чтение версий."""

    def __init__(self, uow: ConfigUowFactory) -> None:
        self._uow = uow

    async def versions(self) -> list[ConfigVersionRow]:
        """Версии, новые первыми."""
        async with self._uow() as uow:
            return await uow.configs.list()

    async def version(self, version: int) -> ConfigVersionRow:
        """Версия с телом.

        Raises:
            ConfigVersionNotFoundError: нет версии.
        """
        async with self._uow() as uow:
            row = await uow.configs.get(version)
        if row is None:
            raise ConfigVersionNotFoundError("версия конфига не найдена")
        return row


class ConfigCommands:
    """Мутации версий: идемпотентны по ``(actor_id, Idempotency-Key)``."""

    def __init__(
        self,
        uow: ConfigUowFactory,
        clock: Clock,
        validate: Validator,
        on_change: ChangeHook | None = None,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._validate = validate
        self._on_change = on_change

    async def save_draft(
        self, raw: Mapping[str, Any], *, actor_id: UUID, reason: str, key: str
    ) -> Json:
        """Сохранить черновик: заменяет текущий, иначе создаёт со следующим номером.

        Raises:
            ConfigInvalidError: тело не проходит проверку.
        """
        body = dict(raw)
        self._validate(body)
        fingerprint = request_fingerprint(
            "config.save_draft", None, {"config": body, "reason": reason}
        )

        async def perform(uow: ConfigUnitOfWork) -> Json:
            await uow.configs.lock_writes()
            row, replaced = await self._put_draft(uow, body, actor_id)
            await self._notify(
                uow, "config.save_draft", row, actor_id, reason, replaced_draft_version=replaced
            )
            return with_body(row)

        return await self._idempotent(fingerprint, actor_id, key, perform)

    async def publish(
        self, version: int, body_hash: str, *, actor_id: UUID, reason: str, key: str
    ) -> Json:
        """Опубликовать черновик; прежняя действующая версия уходит в архив.

        Raises:
            ConfigVersionNotFoundError: нет версии.
            ConfigNotDraftError: версия не черновик.
            DraftChangedError: тело черновика изменилось после просмотра.
        """
        fingerprint = request_fingerprint(
            "config.publish", None, {"version": version, "body_sha256": body_hash, "reason": reason}
        )

        async def perform(uow: ConfigUnitOfWork) -> Json:
            await uow.configs.lock_writes()
            row = await uow.configs.get(version)
            if row is None:
                raise ConfigVersionNotFoundError("версия конфига не найдена")
            if row.status != "draft":
                raise ConfigNotDraftError("публиковать можно только черновик")
            if row.body_sha256 != body_hash:
                raise DraftChangedError("черновик изменился после просмотра")
            self._validate(row.body)
            previous = await uow.configs.published()
            published = await uow.configs.publish(version, now=self._clock.now())
            await self._notify(
                uow,
                "config.publish",
                published,
                actor_id,
                reason,
                previous_version=None if previous is None else previous.version,
                body_sha256=body_hash,
            )
            return with_body(published)

        return await self._idempotent(fingerprint, actor_id, key, perform)

    async def rollback(self, version: int, *, actor_id: UUID, reason: str, key: str) -> Json:
        """Копия когда-то действовавшей версии становится черновиком (заменяет текущий).

        Raises:
            ConfigVersionNotFoundError: нет версии.
            ConfigNeverPublishedError: версия никогда не была действующей.
        """
        fingerprint = request_fingerprint(
            "config.rollback", None, {"version": version, "reason": reason}
        )

        async def perform(uow: ConfigUnitOfWork) -> Json:
            await uow.configs.lock_writes()
            source = await uow.configs.get(version)
            if source is None:
                raise ConfigVersionNotFoundError("версия конфига не найдена")
            if source.status == "draft":
                raise ConfigNeverPublishedError("версия не была действующей")
            row, replaced = await self._put_draft(uow, dict(source.body), actor_id)
            await self._notify(
                uow,
                "config.rollback",
                row,
                actor_id,
                reason,
                source_version=version,
                replaced_draft_version=replaced,
            )
            return with_body(row)

        return await self._idempotent(fingerprint, actor_id, key, perform, status_code=201)

    async def bootstrap(self, raw: Mapping[str, Any], *, actor_id: UUID, reason: str) -> bool:
        """Опубликовать сид, если опубликованной версии нет (local/ci); иначе no-op.

        Те же команды, что у админа, с фиксированными ключами идемпотентности; параллельный
        запуск безопасен (записи сериализует ``lock_writes``).

        Returns:
            ``True``, если версия опубликована сейчас.

        Raises:
            ConfigInvalidError: сид не проходит проверку (ничего не сохранено).
        """
        async with self._uow() as uow:
            if await uow.configs.published() is not None:
                return False
        saved = await self.save_draft(raw, actor_id=actor_id, reason=reason, key="bootstrap:draft")
        await self.publish(
            int(saved["card"]["version"]),
            str(saved["card"]["body_sha256"]),
            actor_id=actor_id,
            reason=reason,
            key="bootstrap:publish",
        )
        return True

    async def _put_draft(
        self, uow: ConfigUnitOfWork, body: dict[str, Any], actor_id: UUID
    ) -> tuple[ConfigVersionRow, int | None]:
        now = self._clock.now()
        sha = body_sha256(body)
        draft = await uow.configs.draft()
        if draft is None:
            return await uow.configs.create_draft(body, sha, actor_id=actor_id, now=now), None
        row = await uow.configs.replace_draft(draft.version, body, sha, actor_id=actor_id, now=now)
        return row, draft.version

    async def _notify(
        self,
        uow: ConfigUnitOfWork,
        action: str,
        row: ConfigVersionRow,
        actor_id: UUID,
        reason: str,
        **details: Any,
    ) -> None:
        if self._on_change is not None:
            await self._on_change(
                uow, ConfigChange(action, row.version, actor_id, reason, dict(details))
            )

    async def _idempotent(
        self,
        fingerprint: str,
        actor_id: UUID,
        key: str,
        perform: Callable[[ConfigUnitOfWork], Awaitable[Json]],
        *,
        status_code: int = 200,
    ) -> Json:
        async with self._uow() as uow:
            response, _ = await run_idempotent(
                uow.idempotency,
                actor_id,
                key,
                fingerprint,
                lambda: perform(uow),
                status_code=status_code,
            )
            await uow.commit()
        return response
