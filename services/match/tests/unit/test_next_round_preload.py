"""Предзагрузка уровня 1 следующего раунда в онлайн-ревиле (ADR 0080)."""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from uuid import UUID

import pytest
from match_doubles import seed_bodies, seed_body
from match_harness import Harness
from match_service.application.views import match_view, messages
from match_service.domain.model import Phase
from match_service.infrastructure.rooms.codec import decode, encode

from gv_core.errors import DependencyUnavailableError
from gv_game_kernel.errors import ConfigError
from gv_testkit import FakeClock
from gv_testkit.game import ClueAsset
from gv_testkit.match import FakeLinkPort

pytestmark = pytest.mark.unit

LINK_TTL_S = 4
MARGIN_MS = 3000
FLAG = "preload_next_round"


class ClockLinks(FakeLinkPort):
    """Ссылки, чей срок считается от часов теста."""

    def __init__(self, clock: FakeClock, ttl_s: int) -> None:
        super().__init__(ttl_s=ttl_s)
        self._clock = clock

    async def link(self, asset: Any, level: int, subject: str) -> ClueAsset:
        self._now = self._clock.now()
        return await super().link(asset, level, subject)


def _harness(bodies: dict[str, dict[str, Any]] | None = None) -> Harness:
    clock = FakeClock(Harness().clock.now())
    return Harness(
        clock=clock,
        assets=ClockLinks(clock, LINK_TTL_S),
        bodies=bodies or seed_bodies(),
        artwork_refresh_margin_ms=MARGIN_MS,
    )


async def _open(h: Harness, key: str = "music.duel.classic") -> tuple[Any, UUID, UUID]:
    room, a = await h.room(key)
    (b,) = await h.join(room, 1)
    await h.start(room, a)
    return room, a, b


async def _finish_round(h: Harness, room: Any, a: UUID, b: UUID) -> None:
    await h.ok(room, a, "guess", **h.guess_data(room, h.title(room)))
    await h.ok(room, b, "guess", **h.guess_data(room, h.title(room)))
    assert room.phase is Phase.REVEAL


def _preload_links(h: Harness) -> list[Any]:
    return [x for x in h.assets.issued if x.subject.endswith(":preload")]


async def test_seeds_enable_flag_only_in_unranked_by_level_duels() -> None:
    enabled = {key: bool(body.get("features", {}).get(FLAG)) for key, body in seed_bodies().items()}
    assert enabled == {
        "music.duel.classic": True,
        "film.duel.frame": True,
        "film.party.frame": False,
        "music.lobby.classic": False,
        "film.lobby.collection": False,
    }
    assert seed_body("music.ranked.classic")["session"]["ranked"] is True
    assert not seed_body("music.ranked.classic").get("features", {}).get(FLAG)


async def test_first_clue_of_next_round_is_issued_at_reveal_with_own_subject() -> None:
    h = _harness()
    room, a, b = await _open(h)
    assert room.match.next_round_first_clue is None and _preload_links(h) == []

    await _finish_round(h, room, a, b)

    (issued,) = _preload_links(h)
    nxt = room.match.plan[1]
    assert issued.level == 1 and issued.subject == f"room:{room.code}:1:preload"
    assert issued.asset_set_id == nxt.asset_for(1)
    assert room.match.next_round_first_clue is not None
    assert room.match.next_round_first_clue.level == 1
    # раунд не открыт, остальные уровни следующего раунда не выданы
    assert room.match.round_index == 0 and room.phase is Phase.REVEAL
    assert [x for x in h.assets.issued if x.asset_set_id == nxt.asset_for(2)] == []


async def test_clue_is_in_http_view_and_realtime_delta_in_level_clue_shape() -> None:
    h = _harness()
    room, a, b = await _open(h)
    assert "next_round_first_clue" not in str(match_view(room))
    room.pull_changes()
    await _finish_round(h, room, a, b)

    reveal = match_view(room)["reveal"]
    assert set(reveal["next_round_first_clue"]) == {
        "level",
        "kind",
        "url",
        "expires_at",
        "mime",
        "meta",
    }
    assert reveal["next_round_first_clue"]["level"] == 1
    changes = [c for c in room.pull_changes() if c.kind == "round_reveal"]
    delta = messages(room, changes, h.now_ms())[0]["delta"]
    assert delta["next_round_first_clue"] == reveal["next_round_first_clue"]


async def test_flag_off_gives_null_and_no_link() -> None:
    bodies = seed_bodies()
    bodies["music.duel.classic"].pop("features", None)
    h = _harness(bodies)
    room, a, b = await _open(h)
    await _finish_round(h, room, a, b)
    assert room.match.next_round_first_clue is None
    assert match_view(room)["reveal"]["next_round_first_clue"] is None
    assert _preload_links(h) == []


async def test_ranked_template_with_flag_is_rejected_by_kernel_validator() -> None:
    bodies = seed_bodies()
    bodies["music.duel.classic"] = seed_body("music.duel.classic", ranked=True, ladder="music")
    assert bodies["music.duel.classic"]["features"][FLAG] is True
    h = _harness(bodies)
    with pytest.raises(ConfigError):
        await h.room()


async def test_ranked_room_gets_no_link_even_if_mode_info_claims_preload() -> None:
    """Второй рубеж: владелец сам не выдаёт ссылку рейтинговой комнате."""
    h = _harness()
    room, a, b = await _open(h)
    room.mode = replace(room.mode, ranked=True)
    assert room.mode.preload_next_round is True
    await _finish_round(h, room, a, b)
    assert room.match.next_round_first_clue is None
    assert match_view(room)["reveal"]["next_round_first_clue"] is None
    assert _preload_links(h) == []


async def test_time_based_template_with_flag_is_rejected_by_kernel_validator() -> None:
    bodies = seed_bodies()
    bodies["film.party.frame"].setdefault("features", {})[FLAG] = True
    h = _harness(bodies)
    with pytest.raises(ConfigError):
        await h.room("film.party.frame")


async def test_last_round_has_no_next_clue() -> None:
    h = _harness()
    room, a, b = await _open(h)
    last = room.match.rounds_total - 1
    while room.match.round_index < last:
        await _finish_round(h, room, a, b)
        await h.until(room, Phase.LEVEL_OPEN)
    await _finish_round(h, room, a, b)
    assert room.match.round_index == last
    assert room.match.next_round_first_clue is None
    assert match_view(room)["reveal"]["next_round_first_clue"] is None


@pytest.mark.parametrize("failure", ["gone", "unavailable"])
async def test_link_failure_gives_null_and_reveal_survives(failure: str) -> None:
    h = _harness()
    room, a, b = await _open(h)
    nxt_set = room.match.plan[1].asset_for(1)
    if failure == "gone":
        h.assets.gone(nxt_set)
    else:
        h.assets.fail(DependencyUnavailableError("media"), asset_set_id=nxt_set)
    await _finish_round(h, room, a, b)
    assert room.match.next_round_first_clue is None
    assert room.match.final_clue is not None


async def test_link_is_reissued_before_expiry_with_same_subject() -> None:
    h = _harness()
    room, a, b = await _open(h)
    await _finish_round(h, room, a, b)
    first = room.match.next_round_first_clue
    assert first is not None

    await h.tick(room, 2)

    assert room.phase is Phase.REVEAL
    assert room.match.next_round_first_clue.expires_at_ms > first.expires_at_ms
    links = _preload_links(h)
    assert len(links) == 2 and {x.subject for x in links} == {f"room:{room.code}:1:preload"}


async def test_failed_reissue_keeps_live_link_and_drops_expired_one() -> None:
    h = _harness()
    room, a, b = await _open(h)
    await _finish_round(h, room, a, b)
    h.assets.unavailable()
    await h.tick(room, 2)
    assert room.match.next_round_first_clue is not None
    await h.tick(room, 2)
    assert room.match.next_round_first_clue is None


async def test_field_is_cleared_when_next_round_opens_and_level_uses_own_link() -> None:
    h = _harness()
    room, a, b = await _open(h)
    await _finish_round(h, room, a, b)
    await h.until(room, Phase.LEVEL_OPEN)
    assert room.match.round_index == 1 and room.match.next_round_first_clue is None
    assert room.match.clue is not None
    assert f"room:{room.code}:1" in h.assets.subjects()  # собственная ссылка уровня


async def test_field_survives_snapshot_round_trip() -> None:
    h = _harness()
    room, a, b = await _open(h)
    await _finish_round(h, room, a, b)
    restored = decode(encode(room))
    assert restored.mode.preload_next_round is True
    assert restored.match.next_round_first_clue == room.match.next_round_first_clue
