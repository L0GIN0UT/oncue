"""Задание переопубликации единиц (``POST /admin/v1/items/republish``, план этапа 3a M9).

Каждая порция выпускает ``MediaItemUpdated`` с ``version+1`` для очередных единиц (по
возрастанию id) и сдвигает курсор. Задача порции несёт курсор, с которого начинала: если
курсор задания уже другой — порция обработана (повтор доставки), задача ничего не делает.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from catalog_service.domain.model import MediaDomain


class RepublishStatus(StrEnum):
    """Состояние задания."""

    RUNNING = "running"
    DONE = "done"


class RepublishJob:
    """Задание переопубликации.

    Attributes:
        domain: Только единицы домена; ``None`` — все.
        cursor: id последней переопубликованной единицы.
        published: Сколько единиц переопубликовано.
    """

    def __init__(
        self,
        id: uuid.UUID,  # noqa: A002
        *,
        domain: MediaDomain | None,
        actor_id: uuid.UUID,
        reason: str,
        status: RepublishStatus = RepublishStatus.RUNNING,
        cursor: uuid.UUID | None = None,
        published: int = 0,
    ) -> None:
        self.id = id
        self.domain = domain
        self.actor_id = actor_id
        self.reason = reason
        self.status = status
        self.cursor = cursor
        self.published = published

    def expects(self, after: uuid.UUID | None) -> bool:
        """Порция с этим курсором ещё не обработана."""
        return self.status is RepublishStatus.RUNNING and self.cursor == after

    def advance(self, ids: list[uuid.UUID], *, done: bool) -> None:
        """Порция обработана."""
        if ids:
            self.cursor = ids[-1]
            self.published += len(ids)
        if done:
            self.status = RepublishStatus.DONE
