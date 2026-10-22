"""Очистка просроченных кодов из писем и отозванных доверенных устройств (ADR 0041)."""

from __future__ import annotations

from datetime import timedelta

import structlog

from gv_core.clock import Clock
from identity_service.application.config import CodeConfig
from identity_service.application.ports import ChallengePurger

log = structlog.get_logger(__name__)


class PurgeEmailChallenges:
    """Периодическая задача: удалить давно истёкшие коды и устройства.

    Код хранится ещё ``purge_retention_s`` (не меньше суток) после истечения: по нему
    считается суточный потолок неудач (``failures_since``). Пачками, не больше
    ``purge_max_batches`` за запуск — большой хвост дочищается следующими запусками.
    """

    def __init__(self, purger: ChallengePurger, clock: Clock, config: CodeConfig) -> None:
        self._purger, self._clock, self._config = purger, clock, config

    async def __call__(self) -> int:
        """Удалить; возвращает число удалённых строк."""
        now = self._clock.now()
        cfg = self._config
        challenges_before = now - timedelta(seconds=cfg.purge_retention_s)
        devices_before = now - timedelta(seconds=cfg.device_revoked_retention_s)
        total = 0
        for _ in range(cfg.purge_max_batches):
            deleted = await self._purger.purge(
                challenges_before=challenges_before,
                devices_revoked_before=devices_before,
                now=now,
                limit=cfg.purge_batch,
            )
            total += deleted
            if deleted < cfg.purge_batch:
                break
        log.info("auth.email_challenges_purged", deleted=total)
        return total
