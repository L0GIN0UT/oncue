"""Версии конфига: черновик, публикация, откат, чтение; задания и бейджи (ADR 0042).

Публикация не пересчитывает прошлое: проводки уже несут свою ``config_version``, а начисление
берёт версию по времени события (``effective_at``). Публикация привязана к содержимому:
админ присылает ``body_sha256`` черновика, который видел; изменённый черновик - 409.
Единая точка проверки конфига - ``ProgressionConfig.parse`` (через ``validate_config``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from uuid import UUID

import structlog

from gv_core.clock import Clock
from gv_core.ids import uuid7
from gv_db.idempotency import request_fingerprint, run_idempotent
from progression_service.application.accrual.common import published_config
from progression_service.application.admin.common import (
    AdminContext,
    ConfigNeverPublishedError,
    ConfigNotDraftError,
    ConfigVersionNotFoundError,
    DraftChangedError,
    audit,
    body_sha256,
    validate_config,
)
from progression_service.application.admin.metrics import ADMIN_OPERATIONS
from progression_service.application.admin.ports import (
    AdminUnitOfWork,
    AdminUowFactory,
    ConfigVersionRow,
    Json,
)

log = structlog.get_logger(__name__)

TARGET = "config"


def card_json(row: ConfigVersionRow) -> Json:
    """Карточка версии (``ConfigVersionCard``) с хешем тела."""
    return {
        "version": row.version,
        "status": row.status,
        "created_at": row.created_at.isoformat(),
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "created_by": str(row.created_by),
        "body_sha256": body_sha256(row.body),
    }


def body_json(row: ConfigVersionRow) -> Json:
    """Версия с телом (``ConfigVersionBody``)."""
    return {"card": card_json(row), "config": row.body}


class ConfigCommands:
    """Мутации версий конфига."""

    def __init__(self, uow: AdminUowFactory, clock: Clock) -> None:
        self._uow = uow
        self._clock = clock

    async def save_draft(self, raw: Mapping[str, Any], *, ctx: AdminContext) -> Json:
        """Сохранить черновик: заменяет текущий, иначе создаёт со следующим номером.

        Raises:
            ConfigDraftInvalidError: конфиг не проходит проверку.
        """
        body = dict(raw)
        validate_config(body)
        fingerprint = request_fingerprint(
            "config.save_draft", TARGET, {"config": body, "reason": ctx.reason}
        )

        async def perform(uow: AdminUnitOfWork) -> Json:
            await uow.config_admin.lock_writes()
            row, replaced = await self._put_draft(uow, body, ctx)
            await audit(
                uow,
                ctx,
                "config.save_draft",
                f"{TARGET}:{row.version}",
                row.created_at,
                action_id=uuid7(),
                replaced_draft_version=replaced,
            )
            return body_json(row)

        response, _ = await self._idempotent(fingerprint, ctx, perform)
        return response

    async def publish(self, version: int, body_hash: str, *, ctx: AdminContext) -> Json:
        """Опубликовать черновик; прежняя действующая версия уходит в архив.

        Args:
            version: Номер черновика.
            body_hash: ``body_sha256`` черновика, который видел админ.
            ctx: Контекст мутации.

        Raises:
            ConfigVersionNotFoundError: нет версии.
            ConfigNotDraftError: версия не черновик.
            DraftChangedError: тело черновика изменилось после просмотра.
        """
        fingerprint = request_fingerprint(
            "config.publish",
            TARGET,
            {"version": version, "body_sha256": body_hash, "reason": ctx.reason},
        )

        async def perform(uow: AdminUnitOfWork) -> Json:
            await uow.config_admin.lock_writes()
            row = await uow.config_admin.get(version)
            if row is None:
                raise ConfigVersionNotFoundError
            if row.status != "draft":
                raise ConfigNotDraftError
            if body_sha256(row.body) != body_hash:
                raise DraftChangedError
            validate_config(row.body)
            now = self._clock.now()
            previous = await uow.configs.published()
            published = await uow.config_admin.publish(version, now=now)
            await audit(
                uow,
                ctx,
                "config.publish",
                f"{TARGET}:{version}",
                now,
                action_id=uuid7(),
                previous_version=None if previous is None else previous.version,
                body_sha256=body_hash,
            )
            return body_json(published)

        response, executed = await self._idempotent(fingerprint, ctx, perform)
        if executed:
            ADMIN_OPERATIONS.labels("config_published", "ok").inc()
            log.info(
                "progression.admin.config_published", actor_id=str(ctx.actor_id), version=version
            )
        return response

    async def bootstrap(self, raw: Mapping[str, Any], *, actor_id: UUID, reason: str) -> bool:
        """Опубликовать сид, если опубликованной версии ещё нет (local/ci); иначе no-op.

        Те же команды, что у админа (черновик + публикация); ключи идемпотентности фиксированы,
        параллельный запуск безопасен: записи сериализует ``lock_writes``.

        Returns:
            ``True``, если версия опубликована сейчас.

        Raises:
            ConfigDraftInvalidError: сид не проходит проверку (ничего не сохранено).
        """
        async with self._uow() as uow:
            if await uow.configs.published() is not None:
                return False
        card = await self.save_draft(
            raw,
            ctx=AdminContext(actor_id=actor_id, reason=reason, idempotency_key="bootstrap:draft"),
        )
        draft = card["card"]
        assert isinstance(draft, dict)  # noqa: S101 - форма ответа save_draft
        await self.publish(
            int(draft["version"]),
            str(draft["body_sha256"]),
            ctx=AdminContext(actor_id=actor_id, reason=reason, idempotency_key="bootstrap:publish"),
        )
        return True

    async def rollback(self, version: int, *, ctx: AdminContext) -> Json:
        """Копия когда-то действовавшей версии становится черновиком (заменяет текущий).

        Raises:
            ConfigVersionNotFoundError: нет версии.
            ConfigNeverPublishedError: версия никогда не была действующей.
        """
        fingerprint = request_fingerprint(
            "config.rollback", TARGET, {"version": version, "reason": ctx.reason}
        )

        async def perform(uow: AdminUnitOfWork) -> Json:
            await uow.config_admin.lock_writes()
            source = await uow.config_admin.get(version)
            if source is None:
                raise ConfigVersionNotFoundError
            if source.status == "draft":
                raise ConfigNeverPublishedError
            row, replaced = await self._put_draft(uow, dict(source.body), ctx)
            await audit(
                uow,
                ctx,
                "config.rollback",
                f"{TARGET}:{version}",
                row.created_at,
                action_id=uuid7(),
                draft_version=row.version,
                replaced_draft_version=replaced,
            )
            return body_json(row)

        response, executed = await self._idempotent(fingerprint, ctx, perform, status_code=201)
        if executed:
            ADMIN_OPERATIONS.labels("config_rolled_back", "ok").inc()
            log.info(
                "progression.admin.config_rolled_back",
                actor_id=str(ctx.actor_id),
                version=version,
            )
        return response

    async def _put_draft(
        self, uow: AdminUnitOfWork, body: dict[str, Any], ctx: AdminContext
    ) -> tuple[ConfigVersionRow, int | None]:
        """Новый черновик или замена существующего; второе - номер заменённого черновика."""
        now = self._clock.now()
        draft = await uow.config_admin.draft()
        if draft is None:
            return await uow.config_admin.create_draft(body, actor_id=ctx.actor_id, now=now), None
        row = await uow.config_admin.replace_draft(
            draft.version, body, actor_id=ctx.actor_id, now=now
        )
        return row, draft.version

    async def _idempotent(
        self,
        fingerprint: str,
        ctx: AdminContext,
        perform: Callable[[AdminUnitOfWork], Awaitable[Json]],
        *,
        status_code: int = 200,
    ) -> tuple[Json, bool]:
        async with self._uow() as uow:
            response, executed = await run_idempotent(
                uow.idempotency,
                ctx.actor_id,
                ctx.idempotency_key,
                fingerprint,
                lambda: perform(uow),
                status_code=status_code,
            )
            await uow.commit()
        return response, executed


class ConfigQueries:
    """Чтение версий, заданий и бейджей для админки."""

    def __init__(self, uow: AdminUowFactory) -> None:
        self._uow = uow

    async def versions(self) -> Json:
        """Версии, новые первыми."""
        async with self._uow() as uow:
            rows = await uow.config_admin.list()
        return {"items": [card_json(r) for r in rows]}

    async def version(self, version: int) -> Json:
        """Версия с телом.

        Raises:
            ConfigVersionNotFoundError: нет версии.
        """
        async with self._uow() as uow:
            row = await uow.config_admin.get(version)
        if row is None:
            raise ConfigVersionNotFoundError
        return body_json(row)

    async def quests(self) -> Json:
        """Задания действующего конфига; ``completed_total = null`` - счётчиков ещё нет."""
        async with self._uow() as uow:
            published = await published_config(uow)
            done = await uow.stats.quest_completions()
        quests = published.config.body.get("quests")
        raw_items = quests.get("items", []) if isinstance(quests, Mapping) else []
        items = []
        for item in raw_items:
            if not isinstance(item, Mapping):
                continue
            reward = item.get("reward") or {}
            items.append(
                {
                    "key": item.get("key"),
                    "period": item.get("period"),
                    "title": item.get("title", ""),
                    "reward": {
                        "xp": int(reward.get("xp", 0)),
                        "verses": int(reward.get("verses", 0)),
                    },
                    "enabled": bool(item.get("enabled", True)),
                    "completed_total": None if done is None else done.get(str(item.get("key")), 0),
                }
            )
        return {"config_version": published.version, "items": items}

    async def badges(self) -> Json:
        """Бейджи действующего конфига; ``unlocked_total = null`` - счётчиков ещё нет."""
        async with self._uow() as uow:
            published = await published_config(uow)
            unlocked = await uow.stats.badge_unlocks()
        raw = published.config.body.get("badges")
        items = [
            {
                "key": item.get("key"),
                "title": item.get("title", ""),
                "enabled": bool(item.get("enabled", True)),
                "unlocked_total": None
                if unlocked is None
                else unlocked.get(str(item.get("key")), 0),
            }
            for item in (raw if isinstance(raw, list) else [])
            if isinstance(item, Mapping)
        ]
        return {"config_version": published.version, "items": items}
