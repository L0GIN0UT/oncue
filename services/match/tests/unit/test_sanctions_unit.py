"""Санкции в match без IO: эффективная маска, шлюз, выбывание по ``evict`` (ADR 0052)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from match_harness import Harness
from match_service.application.sanctions import SanctionGate
from match_service.domain.model import Phase, Presence
from match_service.domain.sanctions import (
    ONLINE_BAN,
    RANKED_BAN,
    SanctionsSnapshot,
    effective_sanctions,
)

from gv_auth import Sanction, SanctionEntry
from gv_core.errors import ForbiddenError
from gv_core.ids import uuid7
from gv_testkit import FakeClock

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 24, 12, tzinfo=UTC)
USER = uuid7()


def snapshot(*entries: SanctionEntry) -> SanctionsSnapshot:
    return SanctionsSnapshot(USER, 1, entries, 0, None)


def test_claim_is_used_without_projection() -> None:
    assert effective_sanctions(None, ONLINE_BAN, NOW) == ONLINE_BAN


def test_projection_overrides_claim_both_ways() -> None:
    lifted = snapshot()
    assert effective_sanctions(lifted, ONLINE_BAN | RANKED_BAN, NOW) == 0
    added = snapshot(SanctionEntry("online", None))
    assert effective_sanctions(added, 0, NOW) == ONLINE_BAN


def test_expired_entries_are_ignored() -> None:
    entries = snapshot(
        SanctionEntry("online", NOW), SanctionEntry("ranked", NOW + timedelta(seconds=1))
    )
    assert effective_sanctions(entries, ONLINE_BAN, NOW) == RANKED_BAN


class Projection:
    def __init__(self, snap: SanctionsSnapshot | None = None, *, fail: bool = False) -> None:
        self.snap, self.fail = snap, fail

    async def get(self, user_id: UUID) -> SanctionsSnapshot | None:
        if self.fail:
            raise RuntimeError("db down")
        return self.snap


async def test_gate_denies_online_ban_with_code() -> None:
    gate = SanctionGate(Projection(snapshot(SanctionEntry("online", None))), FakeClock(NOW))
    with pytest.raises(ForbiddenError) as err:
        await gate.require_online(USER, 0)
    assert err.value.code == "match.online_banned"
    await SanctionGate(Projection(snapshot()), FakeClock(NOW)).require_online(
        USER, int(Sanction.ONLINE)
    )


async def test_gate_falls_back_to_claim_when_projection_unavailable() -> None:
    gate = SanctionGate(Projection(fail=True), FakeClock(NOW))
    assert await gate.mask(USER, int(Sanction.ONLINE)) == ONLINE_BAN
    assert await gate.mask(USER, 0) == 0


async def test_evict_leaves_lobby_and_revokes_subscription() -> None:
    h = Harness()
    room, host = await h.room("film.lobby.collection", collection_ids=(uuid7(),))
    b, c = await h.join(room, 2)
    await h.ok(room, b, "evict")
    seat = room.seat_of(b)
    assert seat is None or seat.presence is Presence.LEFT  # в лобби место освобождается
    assert b not in room.forgotten  # в отличие от remove (удаление аккаунта), без забвения
    assert h.subscriptions.revoked == [(b, f"room:{room.code}")]
    assert await h.player_rooms.get(b) is None
    del host, c


async def test_evict_mid_match_leaves_the_match_and_is_idempotent_for_outsiders() -> None:
    h = Harness()
    room, host = await h.room()
    (b,) = await h.join(room, 1)
    await h.start(room, host)
    await h.ok(room, b, "evict")
    assert room.phase is Phase.SUMMARY  # дуэль без соперника завершается
    await h.ok(room, b, "evict")  # повтор безопасен
    stranger = uuid7()
    await h.ok(room, stranger, "evict")  # игрок не из комнаты - не ошибка
