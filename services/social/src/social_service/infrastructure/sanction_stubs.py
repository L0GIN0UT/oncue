"""Временные заглушки портов санкций до подключения реализации потока F3 (ADR 0052).

Читатель не видит санкций (маска 0), выдача отвечает 501. Оркестратор при слиянии заменяет их
реализацией F3 в ``Adapters`` (``sanctions``, ``granter_factory``).
"""

from __future__ import annotations

from uuid import UUID

from gv_core.errors import NotImplementedYetError
from social_service.application.ports.discussion import DiscussionUnitOfWork
from social_service.application.ports.sanctions import GrantRequest, SanctionGranter, SanctionRecord


class NoSanctionsReader:
    """Нет действующих санкций (до реализации F3)."""

    async def active_mask(self, user_id: UUID) -> int:  # noqa: ARG002 - порт
        """Всегда 0."""
        return 0


class UnimplementedGranter:
    """Выдача санкций ещё не подключена."""

    async def grant(self, request: GrantRequest) -> SanctionRecord:  # noqa: ARG002 - порт
        """Всегда 501 ``common.not_implemented``."""
        raise NotImplementedYetError("выдача санкций появится в потоке F3 (5c)")


def unimplemented_granter(_uow: DiscussionUnitOfWork) -> SanctionGranter:
    """Фабрика заглушки для ``CaseService``."""
    return UnimplementedGranter()
