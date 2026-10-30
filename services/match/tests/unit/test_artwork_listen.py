"""Картинка ответа в онлайн-ревиле (ADR 0073) и позиция прослушивания (`listen_s`)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from match_harness import Harness
from match_service.application.views import match_view, messages, room_view
from match_service.domain.model import Phase

from gv_core.errors import DependencyUnavailableError, RateLimitedError
from gv_testkit import FakeClock
from gv_testkit.game import ClueAsset
from gv_testkit.match import FakeLinkPort

pytestmark = pytest.mark.unit

LINK_TTL_S = 4
MARGIN_MS = 3000


class ClockLinks(FakeLinkPort):
    """Ссылки, чей срок считается от часов теста (а не от фиксированного момента)."""

    def __init__(self, clock: FakeClock, ttl_s: int) -> None:
        super().__init__(ttl_s=ttl_s)
        self._clock = clock

    async def link(self, asset: Any, level: int, subject: str) -> ClueAsset:
        self._now = self._clock.now()
        return await super().link(asset, level, subject)


def _harness() -> Harness:
    clock = FakeClock(Harness().clock.now())
    return Harness(
        clock=clock,
        assets=ClockLinks(clock, LINK_TTL_S),
        artwork_sets=True,
        artwork_refresh_margin_ms=MARGIN_MS,
    )


async def _open(h: Harness) -> tuple[Any, UUID, UUID]:
    """Дуэль с открытым уровнем 1."""
    room, a = await h.room()
    (b,) = await h.join(room, 1)
    await h.start(room, a)
    return room, a, b


async def _finish_round(h: Harness, room: Any, a: UUID, b: UUID) -> None:
    await h.ok(room, a, "guess", **h.guess_data(room, h.title(room)))
    await h.ok(room, b, "guess", **h.guess_data(room, h.title(room)))
    assert room.phase is Phase.REVEAL


def _artwork_links(h: Harness, room: Any) -> list[Any]:
    sets = {r.artwork_asset_set_id for r in room.match.plan}
    return [x for x in h.assets.issued if x.asset_set_id in sets]


# ----------------------------------------------------------------------------- artwork
async def test_artwork_is_issued_only_at_reveal_with_reveal_subject() -> None:
    h = _harness()
    room, a, b = await _open(h)
    assert room.match.artwork is None and _artwork_links(h, room) == []
    assert "artwork" not in str(match_view(room))

    await _finish_round(h, room, a, b)

    (issued,) = _artwork_links(h, room)
    assert issued.level == 1 and issued.subject == f"room:{room.code}:0:reveal"
    assert room.match.artwork is not None


async def test_artwork_in_http_view_and_realtime_message() -> None:
    h = _harness()
    room, a, b = await _open(h)
    room.pull_changes()
    await _finish_round(h, room, a, b)

    reveal = match_view(room)["reveal"]
    assert set(reveal["artwork"]) == {"url", "expires_at", "mime"}
    changes = [c for c in room.pull_changes() if c.kind == "round_reveal"]
    delta = messages(room, changes, h.now_ms())[0]["delta"]
    assert delta["artwork"] == reveal["artwork"]


async def test_plan_without_artwork_set_gives_null_and_no_link() -> None:
    h = Harness()
    room, a, b = await _open(h)
    await _finish_round(h, room, a, b)
    assert room.match.artwork is None
    assert match_view(room)["reveal"]["artwork"] is None
    assert room.match.plan[0].artwork_asset_set_id is None
    assert _artwork_links(h, room) == []


@pytest.mark.parametrize("failure", ["gone", "unavailable", "rate_limited"])
async def test_artwork_failure_gives_null_and_reveal_survives(failure: str) -> None:
    h = _harness()
    room, a, b = await _open(h)
    art_set = room.match.plan[0].artwork_asset_set_id
    assert art_set is not None
    if failure == "gone":
        h.assets.gone(art_set)
    elif failure == "unavailable":
        h.assets.fail(DependencyUnavailableError("media"), asset_set_id=art_set)
    else:
        h.assets.fail(RateLimitedError("лимит"), asset_set_id=art_set)

    await _finish_round(h, room, a, b)

    assert room.match.artwork is None
    assert room.match.final_clue is not None  # ревил цел
    assert match_view(room)["reveal"]["artwork"] is None


async def test_artwork_link_is_reissued_before_expiry_with_same_subject() -> None:
    h = _harness()
    room, a, b = await _open(h)
    await _finish_round(h, room, a, b)
    first = room.match.artwork
    assert first is not None

    await h.tick(room, 2)  # осталось меньше запаса -> перевыдача

    assert room.phase is Phase.REVEAL
    assert room.match.artwork is not None
    assert room.match.artwork.expires_at_ms > first.expires_at_ms
    links = _artwork_links(h, room)
    assert len(links) == 2 and {x.subject for x in links} == {f"room:{room.code}:0:reveal"}


async def test_failed_reissue_keeps_live_link_and_drops_expired_one() -> None:
    h = _harness()
    room, a, b = await _open(h)
    await _finish_round(h, room, a, b)
    h.assets.unavailable()

    await h.tick(room, 2)  # ссылка ещё жива (4 с), перевыдача не удалась
    assert room.phase is Phase.REVEAL and room.match.artwork is not None
    await h.tick(room, 2)  # истекла и не перевыдана
    assert room.match.artwork is None


async def test_artwork_is_cleared_when_next_round_opens() -> None:
    h = _harness()
    room, a, b = await _open(h)
    await _finish_round(h, room, a, b)
    await h.until(room, Phase.LEVEL_OPEN)
    assert room.match.round_index == 1 and room.match.artwork is None


# ----------------------------------------------------------------------------- listen
async def test_listen_sets_rounded_value_and_publishes_player_delta() -> None:
    h = Harness()
    room, a, b = await _open(h)
    room.pull_changes()
    before = room.version

    body = await h.ok(room, a, "listen", level=1, position_ms=2900)

    assert room.seat_of(a).listen_s == 2 and body["version"] == before + 1
    (change,) = room.pull_changes()
    message = messages(room, [change], h.now_ms())[0]
    assert message["kind"] == "player"
    assert message["delta"]["user_id"] == str(a) and message["delta"]["listen_s"] == 2
    players = {p["user_id"]: p["listen_s"] for p in room_view(room, h.now_ms())["players"]}
    assert players == {str(a): 2, str(b): None}


async def test_listen_repeat_is_idempotent_and_last_value_wins() -> None:
    h = Harness()
    room, a, _ = await _open(h)
    await h.ok(room, a, "listen", level=1, position_ms=2100)
    version = room.version

    same = await h.ok(room, a, "listen", level=1, position_ms=2900)  # то же округление
    first = await h.cmd(room, a, "listen", op_id="same", level=1, position_ms=3100)
    dup = await h.cmd(room, a, "listen", op_id="same", level=1, position_ms=3100)

    assert same["version"] == version
    assert first["status"] == 200 and dup["status"] == 200
    assert room.seat_of(a).listen_s == 3


async def test_listen_is_clamped_to_clip_length() -> None:
    h = Harness()
    room, a, _ = await _open(h)
    await h.ok(room, a, "listen", level=1, position_ms=600_000)
    assert room.seat_of(a).listen_s == 4  # seconds уровня 1 = 4 при окне 20 с


async def test_listen_stale_level_ignored_and_wrong_phase_conflicts() -> None:
    h = Harness()
    room, a, _ = await _open(h)
    version = room.version
    stale = await h.ok(room, a, "listen", level=2, position_ms=1000)
    assert stale["version"] == version and room.seat_of(a).listen_s is None

    lobby, host = await h.room(code="ABCDEFGH")
    denied = await h.cmd(lobby, host, "listen", level=1, position_ms=1000)
    assert denied["status"] == 409 and denied["code"] == "match.phase_mismatch"


async def test_listen_only_for_participant() -> None:
    h = Harness()
    room, _, _ = await _open(h)
    outsider = (await h.room(code="ZZZZZZZZ"))[1]
    reply = await h.cmd(room, outsider, "listen", level=1, position_ms=1000)
    assert reply["status"] == 403 and reply["code"] == "match.not_participant"


async def test_listen_resets_on_level_change_and_is_not_kept_in_op_window() -> None:
    h = Harness()
    room, a, _ = await _open(h)
    await h.ok(room, a, "listen", op_id="op-listen", level=1, position_ms=3000)
    assert room.seat_of(a).listen_s == 3
    assert room.op_reply(a, "op-listen") is None

    await h.ok(room, a, "guess", **h.guess_data(room, h.title(room)))  # уровень закрыт
    assert all(s.listen_s is None for s in room.seats)
