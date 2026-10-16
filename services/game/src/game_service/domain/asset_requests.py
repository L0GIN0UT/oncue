"""Запросы ассетов под параметры загадки черновика конфига (D8, ADR 0025).

Админ явно запрашивает генерацию: game пишет строку запроса и событие
``game.ClueAssetsRequested`` в одной транзакции. Пока ассетов под ``params_hash`` нет,
запрос ``pending`` — повторный запрос тех же параметров отвечает им же, без нового
события. Первое ``AssetsReady`` под этот хэш переводит запрос в ``sent``: media-pipeline
завёл профиль и отдаёт наборы.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from game_service.domain.events import ClueAssetsRequested
from gv_core.domain import AggregateRoot


class AssetRequestStatus(StrEnum):
    """Статус запроса ассетов."""

    PENDING = "pending"  # событие отправлено, ассетов под хэш ещё нет
    SENT = "sent"  # media-pipeline прислал AssetsReady под этот хэш


class AssetRequest(AggregateRoot[UUID]):
    """Запрос ассетов под параметры одной загадки.

    Attributes:
        mode_key: Режим черновика.
        draft_revision: Ревизия черновика, из которой взяты параметры.
        domain: Домен режима.
        clue_type: Тип загадки.
        params: Параметры загадки (уже прошли схему плагина).
        params_hash: ``params_hash(params)``.
        status: ``pending`` | ``sent``.
        actor_id: Кто запросил.
        reason: Причина (аудит).
        requested_at: Когда.
    """

    def __init__(
        self,
        request_id: UUID,
        *,
        mode_key: str,
        draft_revision: int,
        domain: str,
        clue_type: str,
        params: Mapping[str, Any],
        params_hash: str,
        status: AssetRequestStatus,
        actor_id: UUID,
        reason: str,
        requested_at: datetime,
    ) -> None:
        super().__init__(request_id)
        self.mode_key = mode_key
        self.draft_revision = draft_revision
        self.domain = domain
        self.clue_type = clue_type
        self.params = dict(params)
        self.params_hash = params_hash
        self.status = status
        self.actor_id = actor_id
        self.reason = reason
        self.requested_at = requested_at

    @classmethod
    def create(
        cls,
        request_id: UUID,
        *,
        mode_key: str,
        draft_revision: int,
        domain: str,
        clue_type: str,
        params: Mapping[str, Any],
        params_hash: str,
        actor_id: UUID,
        reason: str,
        now: datetime,
    ) -> AssetRequest:
        """Новый запрос и событие ``ClueAssetsRequested`` (``aggregate_id`` — ``mode_key``)."""
        request = cls(
            request_id,
            mode_key=mode_key,
            draft_revision=draft_revision,
            domain=domain,
            clue_type=clue_type,
            params=params,
            params_hash=params_hash,
            status=AssetRequestStatus.PENDING,
            actor_id=actor_id,
            reason=reason,
            requested_at=now,
        )
        request.record(
            ClueAssetsRequested(
                aggregate_id=mode_key,
                request_id=request_id,
                mode_key=mode_key,
                draft_revision=draft_revision,
                domain=domain,
                clue_type=clue_type,
                params=dict(params),
                params_hash=params_hash,
                actor_id=actor_id,
                requested_at=now,
            )
        )
        return request
