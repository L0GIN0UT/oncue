"""Бейджи: выдача по метрикам и чтение (ADR 0042)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from progression_service.application.accrual.common import published_config
from progression_service.application.ports import (
    ProgressionUnitOfWork,
    PublishedConfig,
    UnitOfWorkFactory,
)
from progression_service.domain import badges
from progression_service.domain.events import BadgeUnlocked


async def unlock_badges(
    uow: ProgressionUnitOfWork,
    published: PublishedConfig,
    user_id: UUID,
    values: Mapping[str, int],
    now: datetime,
) -> list[str]:
    """Выдать бейджи, цели которых достигнуты; вернуть ключи выданных.

    Выдача один раз: PK ``(user_id, badge_key)`` и ``ON CONFLICT DO NOTHING``; событие
    публикуется только за реально выданный бейдж.
    """
    have = await uow.badges.unlocked(user_id)
    given: list[str] = []
    for badge in badges.newly_unlocked(published.config.badges, values, have):
        if not await uow.badges.add(user_id, badge.key, published.version, now):
            continue
        uow.publish(
            BadgeUnlocked(
                aggregate_id=str(user_id),
                user_id=user_id,
                badge_key=badge.key,
                config_version=published.version,
                at=now,
            )
        )
        given.append(badge.key)
    return given


@dataclass(frozen=True, slots=True)
class BadgeItem:
    """Бейдж конфига с прогрессом игрока."""

    key: str
    title: str
    description: str
    target: int
    progress: int
    unlocked_at: datetime | None


class BadgeQueries:
    """Чтение бейджей игрока."""

    def __init__(self, uow: UnitOfWorkFactory) -> None:
        self._uow = uow

    async def my_badges(self, user_id: UUID) -> list[BadgeItem]:
        """Включённые бейджи конфига и уже полученные (даже если их потом выключили).

        Raises:
            ConfigUnavailableError: нет опубликованного конфига.
        """
        async with self._uow() as uow:
            published = await published_config(uow)
            values = await uow.metrics.values(user_id)
            have = await uow.badges.unlocked(user_id)
        return [
            BadgeItem(
                key=b.key,
                title=b.title,
                description=b.description,
                target=b.target,
                progress=b.target if b.key in have else min(b.target, values.get(b.metric, 0)),
                unlocked_at=have.get(b.key),
            )
            for b in published.config.badges
            if b.enabled or b.key in have
        ]
