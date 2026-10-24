"""Use-cases, нужные слою API (собираются в composition root ``main.py``)."""

from __future__ import annotations

from dataclasses import dataclass

from notifications_service.application.broadcasts import BroadcastQueries, CreateBroadcast
from notifications_service.application.player import (
    DeviceService,
    NotificationQueries,
    PreferenceService,
)
from notifications_service.application.unsubscribe import Unsubscribe


@dataclass(frozen=True, slots=True)
class ApiServices:
    """Все use-cases публичного и админского API."""

    queries: NotificationQueries
    preferences: PreferenceService
    devices: DeviceService
    unsubscribe: Unsubscribe
    create_broadcast: CreateBroadcast
    broadcast_queries: BroadcastQueries
