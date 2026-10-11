"""Конфиги режимов: проверка, черновик, публикация, откат, архив, bootstrap сидов (D8).

Мутации admin — идемпотентны (ключ и ответ в транзакции мутации, ``gv_db.idempotency``)
и пишут аудит в ``admin_actions``. После публикации и архивации сбрасывается кэш конфигов
во всех процессах (поколение в Redis).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog
import yaml

from game_service.application.ports import (
    AdminAction,
    GameUnitOfWork,
    Json,
    ModeConfigSource,
    UnitOfWorkFactory,
)
from game_service.application.rules import RulesFactory
from game_service.domain.errors import (
    ConfigTooLargeError,
    ModeNotFoundError,
    ModeVersionNotFoundError,
)
from game_service.domain.modes import ConfigVersion, Draft, ModeLine
from gv_core.clock import Clock
from gv_db.idempotency import request_fingerprint, run_idempotent
from gv_game_kernel import ConfigError

log = structlog.get_logger(__name__)

KEY_MISMATCH = "config.key_mismatch"
POOL_TOO_SMALL = "config.pool_too_small"
MESSAGE_KEY_PREFIX = "admin.mode_config.errors."


# ---------------------------------------------------------------- JSON для admin-API
def version_summary_json(v: ConfigVersion) -> Json:
    """Строка версии."""
    return {
        "version": v.version,
        "status": str(v.status),
        "body_hash": v.body_hash,
        "actor_id": str(v.actor_id) if v.actor_id else None,
        "reason": v.reason,
        "created_at": v.created_at.isoformat(),
        "rolled_back_from": v.rolled_back_from,
    }


def version_json(v: ConfigVersion) -> Json:
    """Версия с телом."""
    return {**version_summary_json(v), "key": v.key, "body": v.body}


def draft_json(d: Draft) -> Json:
    """Черновик."""
    return {
        "body": d.body,
        "revision": d.revision,
        "validation": d.validation,
        "updated_by": str(d.updated_by) if d.updated_by else None,
        "updated_at": d.updated_at.isoformat(),
    }


def summary_json(line: ModeLine) -> Json:
    """Строка списка режимов."""
    moments = [
        m
        for m in (line.latest and line.latest.created_at, line.draft and line.draft.updated_at)
        if m
    ]
    return {
        "key": line.key,
        "domain": line.domain,
        "status": str(line.status),
        "published_version": line.published_version,
        "has_draft": line.draft is not None,
        "updated_at": max(moments).isoformat() if moments else None,
    }


# ---------------------------------------------------------------- проверка
@dataclass(frozen=True, slots=True)
class ConfigLimits:
    """Ограничения тела конфига."""

    max_bytes: int


class ValidateModeConfig:
    """Проверка тела конфига: схема и реестр (ядро), ключ, размер пула по проекции.

    Args:
        factory: Сборка правил.
        limits: Ограничения тела.
    """

    def __init__(self, factory: RulesFactory, limits: ConfigLimits) -> None:
        self._factory = factory
        self._limits = limits

    def check_size(self, body: Mapping[str, Any]) -> None:
        """Тело не больше ``GV_GAME__MODE_CONFIG_MAX_BYTES`` (§9 п. 9).

        Raises:
            ConfigTooLargeError: тело больше лимита.
        """
        size = len(json.dumps(body, ensure_ascii=False).encode())
        if size > self._limits.max_bytes:
            raise ConfigTooLargeError(
                "тело конфига больше лимита", details={"max_bytes": self._limits.max_bytes}
            )

    async def execute(
        self,
        uow: GameUnitOfWork,
        body: Mapping[str, Any],
        *,
        key: str | None = None,
        check_pool: bool = True,
    ) -> Json:
        """Итог проверки ``{ok, errors, warnings, pool}``.

        Args:
            uow: Открытая транзакция (подсчёт пула по проекции).
            body: Тело конфига.
            key: Ожидаемый ключ (из пути); ``None`` — не сверять.
            check_pool: Пул меньше ``min_playable`` — ошибка; иначе — предупреждение
                (bootstrap сидов в local/ci, когда контент ещё не загружен).
        """
        self.check_size(body)
        check = self._factory.check(body)
        errors = list(check.errors)
        warnings: list[dict[str, str]] = []
        if check.config is not None and key is not None and check.config.key != key:
            errors.append({"path": "key", "code": KEY_MISMATCH})
        pool_info: Json | None = None
        rules = check.rules
        if rules is not None:
            spec = rules.pool
            if spec is not None:
                count = await uow.projection.count_candidates(
                    spec.query(rules.domain, rules.playable_for)
                )
                pool_info = {"playable_count": count}
                minimum = max(spec.min_playable or 0, rules.rounds)
                if count < minimum:
                    if check_pool:
                        errors.append(
                            {"path": "source.params.pool.min_playable", "code": POOL_TOO_SMALL}
                        )
                    else:
                        warnings.append({"code": POOL_TOO_SMALL})
        return {
            "ok": not errors,
            "errors": [{**e, "message_key": MESSAGE_KEY_PREFIX + e["code"]} for e in errors],
            "warnings": warnings,
            "pool": pool_info,
        }


# ---------------------------------------------------------------- мутации
@dataclass(frozen=True, slots=True)
class AdminContext:
    """Кто и зачем выполняет admin-мутацию."""

    actor_id: UUID
    reason: str
    idempotency_key: str


class ModeConfigCommands:
    """``SaveDraft``, ``PublishModeConfig``, ``RollbackModeConfig``, ``ArchiveModeConfig``.

    Args:
        uow: Фабрика транзакций.
        clock: Часы.
        validator: Проверка тела.
        source: Кэш конфигов (сброс после публикации).
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        validator: ValidateModeConfig,
        source: ModeConfigSource,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._validator = validator
        self._source = source

    async def _audit(
        self, uow: GameUnitOfWork, ctx: AdminContext, action: str, key: str, **details: Any
    ) -> None:
        await uow.audit.record(
            AdminAction(
                actor_id=ctx.actor_id,
                action=action,
                target=key,
                reason=ctx.reason,
                at=self._clock.now(),
                details=details,
            )
        )

    async def save_draft(
        self, key: str, body: dict[str, Any], *, base_revision: int | None, ctx: AdminContext
    ) -> Json:
        """Сохранить черновик (и невалидный); ответ — черновик с итогом проверки."""
        self._validator.check_size(body)
        fingerprint = request_fingerprint(
            "mode_config.save_draft",
            key,
            {"body": body, "base": base_revision, "reason": ctx.reason},
        )
        async with self._uow() as uow:

            async def perform() -> Json:
                line = await uow.modes.get_line(key, for_update=True)
                validation = await self._validator.execute(uow, body, key=key)
                draft = line.save_draft(
                    body,
                    base_revision=base_revision,
                    validation=validation,
                    actor_id=ctx.actor_id,
                    now=self._clock.now(),
                )
                await uow.modes.save_line(line)
                await self._audit(uow, ctx, "mode_config.save_draft", key, revision=draft.revision)
                return draft_json(draft)

            response, _ = await run_idempotent(
                uow.idempotency, ctx.actor_id, ctx.idempotency_key, fingerprint, perform
            )
            await uow.commit()
        return response

    async def publish(self, key: str, *, draft_revision: int, ctx: AdminContext) -> Json:
        """Опубликовать валидный черновик ревизии ``draft_revision``.

        Raises:
            ConfigError: черновик невалиден на момент публикации (пул мог уменьшиться).
        """
        fingerprint = request_fingerprint(
            "mode_config.publish", key, {"revision": draft_revision, "reason": ctx.reason}
        )
        async with self._uow() as uow:

            async def perform() -> Json:
                line = await uow.modes.get_line(key, for_update=True)
                draft = line.require_draft(draft_revision)
                validation = await self._validator.execute(uow, draft.body, key=key)
                if not validation["ok"]:
                    raise ConfigError(details={"errors": validation["errors"]})
                version = line.publish_draft(
                    draft_revision, actor_id=ctx.actor_id, reason=ctx.reason, now=self._clock.now()
                )
                await uow.modes.save_line(line)
                uow.track(line)
                await self._audit(uow, ctx, "mode_config.publish", key, version=version.version)
                return version_json(version)

            response, executed = await run_idempotent(
                uow.idempotency, ctx.actor_id, ctx.idempotency_key, fingerprint, perform
            )
            await uow.commit()
        if executed:
            await self._source.invalidate()
            log.info("game.mode_published", mode_key=key, version=response["version"])
        return response

    async def rollback(self, key: str, *, to_version: int, ctx: AdminContext) -> Json:
        """Откат: копия версии ``to_version`` как новая версия (тело должно собираться)."""
        fingerprint = request_fingerprint(
            "mode_config.rollback", key, {"to": to_version, "reason": ctx.reason}
        )
        async with self._uow() as uow:

            async def perform() -> Json:
                line = await uow.modes.get_line(key, for_update=True)
                if line.latest is None:
                    raise ModeNotFoundError("режим не найден")
                source = await uow.modes.get_version(key, to_version)
                if source is None:
                    raise ModeVersionNotFoundError("версия конфига не найдена")
                validation = await self._validator.execute(uow, source.body, key=key)
                if not validation["ok"]:
                    raise ConfigError(details={"errors": validation["errors"]})
                version = line.rollback(
                    source, actor_id=ctx.actor_id, reason=ctx.reason, now=self._clock.now()
                )
                await uow.modes.save_line(line)
                uow.track(line)
                await self._audit(
                    uow, ctx, "mode_config.rollback", key, version=version.version, to=to_version
                )
                return version_json(version)

            response, executed = await run_idempotent(
                uow.idempotency, ctx.actor_id, ctx.idempotency_key, fingerprint, perform
            )
            await uow.commit()
        if executed:
            await self._source.invalidate()
        return response

    async def archive(self, key: str, *, ctx: AdminContext) -> Json:
        """Убрать режим из каталога (сессии доигрываются)."""
        fingerprint = request_fingerprint("mode_config.archive", key, {"reason": ctx.reason})
        async with self._uow() as uow:

            async def perform() -> Json:
                line = await uow.modes.get_line(key, for_update=True)
                if line.latest is None and line.draft is None:
                    raise ModeNotFoundError("режим не найден")
                archived = line.archive(actor_id=ctx.actor_id, now=self._clock.now())
                await uow.modes.save_line(line)
                uow.track(line)
                await self._audit(uow, ctx, "mode_config.archive", key, version=archived.version)
                return summary_json(line)

            response, executed = await run_idempotent(
                uow.idempotency, ctx.actor_id, ctx.idempotency_key, fingerprint, perform
            )
            await uow.commit()
        if executed:
            await self._source.invalidate()
        return response


# ---------------------------------------------------------------- чтение для админки
class ModeConfigQueries:
    """Списки и карточки конфигов для админки."""

    def __init__(self, uow: UnitOfWorkFactory, validator: ValidateModeConfig) -> None:
        self._uow = uow
        self._validator = validator

    async def list_modes(self, *, domain: str | None, status: str | None) -> list[Json]:
        """Режимы с фильтрами."""
        async with self._uow() as uow:
            lines = await uow.modes.list_lines()
        return [
            summary_json(line)
            for line in lines
            if (domain is None or line.domain == domain)
            and (status is None or str(line.status) == status)
        ]

    async def detail(self, key: str) -> Json:
        """Карточка режима: версии и черновик.

        Raises:
            ModeNotFoundError: нет ни версий, ни черновика.
        """
        async with self._uow() as uow:
            line = await uow.modes.get_line(key)
            versions = await uow.modes.list_versions(key)
        if line.latest is None and line.draft is None:
            raise ModeNotFoundError("режим не найден")
        return {
            "key": key,
            "domain": line.domain,
            "status": str(line.status),
            "published_version": line.published_version,
            "versions": [version_summary_json(v) for v in versions],
            "draft": draft_json(line.draft) if line.draft else None,
        }

    async def version(self, key: str, version: int) -> Json:
        """Версия с телом.

        Raises:
            ModeVersionNotFoundError: версии нет.
        """
        async with self._uow() as uow:
            found = await uow.modes.get_version(key, version)
        if found is None:
            raise ModeVersionNotFoundError("версия конфига не найдена")
        return version_json(found)

    async def validate(self, body: dict[str, Any]) -> Json:
        """Проверить тело без сохранения."""
        async with self._uow() as uow:
            return await self._validator.execute(uow, body)


# ---------------------------------------------------------------- bootstrap
def _read_seeds(directory: Path) -> list[tuple[str, Any]]:
    """Сиды ``*.yaml`` каталога по имени файла (чтение — в потоке, вне event loop)."""
    return [
        (path.name, yaml.safe_load(path.read_text(encoding="utf-8")))
        for path in sorted(directory.glob("*.yaml"))
    ]


class BootstrapModes:
    """Публикация сидов режимов из каталога (только local/ci, D8).

    Публикует режим, только если у него ещё нет ни одной версии: повторный запуск и
    несколько реплик безопасны (advisory lock ключа + проверка в транзакции). Пул не
    проверяется — контент в local/ci загружается после старта сервиса.
    """

    def __init__(
        self,
        uow: UnitOfWorkFactory,
        clock: Clock,
        validator: ValidateModeConfig,
        source: ModeConfigSource,
    ) -> None:
        self._uow = uow
        self._clock = clock
        self._validator = validator
        self._source = source

    async def execute(self, directory: Path) -> list[str]:
        """Опубликовать новые режимы из ``*.yaml`` каталога; вернуть их ключи."""
        published: list[str] = []
        for name, body in await asyncio.to_thread(_read_seeds, directory):
            if not isinstance(body, dict) or not isinstance(body.get("key"), str):
                log.error("game.bootstrap_invalid_file", file=name)
                continue
            key = body["key"]
            async with self._uow() as uow:
                line = await uow.modes.get_line(key, for_update=True)
                if line.latest is not None:
                    continue
                validation = await self._validator.execute(uow, body, key=key, check_pool=False)
                if not validation["ok"]:
                    log.error(
                        "game.bootstrap_config_invalid",
                        mode_key=key,
                        errors=[e["path"] + ":" + e["code"] for e in validation["errors"]],
                    )
                    continue
                line.publish(body, actor_id=None, reason="bootstrap", now=self._clock.now())
                await uow.modes.save_line(line)
                uow.track(line)
                await uow.commit()
            published.append(key)
        if published:
            await self._source.invalidate()
            log.info("game.modes_bootstrapped", modes=published)
        return published
