"""Санкции в match (ADR 0052): проекция ``social.UserSanctionsChanged``, ONLINE на входах,
выбивание из очереди и лобби, эффективная маска «проекция авторитетна, иначе claim»."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from match_app import MatchApp, Player
from match_service.infrastructure.container import container_for
from sqlalchemy import text

from gv_auth import Sanction
from gv_core.ids import uuid7
from gv_messaging import Outcome
from gv_testkit import assert_problem, load_example

pytestmark = pytest.mark.component

ONLINE = int(Sanction.ONLINE)
CHAT = int(Sanction.CHAT_MUTE | Sanction.COMMENTS)
LOBBY = "film.lobby.collection"


async def sanction(
    svc: MatchApp,
    user: Player,
    version: int,
    *active: tuple[str, int | None],
    fresh: bool = True,
) -> Outcome:
    """Событие с активными санкциями ``(вид, срок в секундах от «сейчас» | None)``."""
    from gv_auth import mask_of_kinds

    now = svc.clock.now()
    entries = [
        {
            "kind": kind,
            "expires_at": (now + timedelta(seconds=ttl)).isoformat() if ttl is not None else None,
            "sanction_id": str(uuid7()),
        }
        for kind, ttl in active
    ]
    expiries = [e["expires_at"] for e in entries if e["expires_at"]]
    base = load_example("social.UserSanctionsChanged", fresh_id=fresh)
    payload = {
        **base.payload,
        "user_id": str(user.user_id),
        "version": version,
        "active": entries,
        "mask": mask_of_kinds(k for k, _ in active),
        "account_banned": any(k == "account" for k, _ in active),
        "next_expiry_at": min(expiries) if expiries else None,
        "at": now.isoformat(),
    }
    return await svc.deliver(
        base.model_copy(update={"aggregate_id": str(user.user_id), "payload": payload})
    )


async def _create(svc: MatchApp, player: Player) -> Any:
    return await svc.client.post(
        "/v1/rooms",
        json={"mode_key": "music.duel.classic"},
        headers={**player.headers, "Idempotency-Key": player.op()},
    )


async def test_online_claim_closes_every_entry_point(svc: MatchApp) -> None:
    await svc.ladder("music")
    banned = svc.user(sanctions=ONLINE)
    host = svc.user()
    code = (await svc.create(host, "music.duel.classic"))["code"]

    assert_problem(await _create(svc, banned), 403, "match.online_banned")
    assert_problem(await svc.post(banned, code, "join"), 403, "match.online_banned")
    assert_problem(await svc.post(banned, code, "rematch"), 403, "match.online_banned")
    assert_problem(await svc.enter(banned), 403, "match.online_banned")
    token = await svc.client.post(f"/v1/rooms/{code}/realtime-token", headers=banned.headers)
    assert_problem(token, 403, "match.online_banned")
    # Чужие санкции (чат, рейтинг) онлайн не закрывают.
    assert (await _create(svc, svc.user(sanctions=CHAT))).status_code == 201


async def test_projection_closes_entry_points_when_token_is_clean(svc: MatchApp) -> None:
    await svc.ladder("music")
    player, host = svc.user(), svc.user()
    code = (await svc.create(host, "music.duel.classic"))["code"]
    assert await sanction(svc, player, 1, ("online", None)) is Outcome.PROCESSED
    assert_problem(await _create(svc, player), 403, "match.online_banned")
    assert_problem(await svc.post(player, code, "join"), 403, "match.online_banned")
    assert_problem(await svc.enter(player), 403, "match.online_banned")


async def test_projection_is_authoritative_for_lifting(svc: MatchApp) -> None:
    """Токен ещё несёт ONLINE (≤ 15 мин), а снятие уже в проекции: доступ открыт."""
    player = svc.user(sanctions=ONLINE)
    assert_problem(await _create(svc, player), 403, "match.online_banned")
    await sanction(svc, player, 1, ("online", None))
    await sanction(svc, player, 2)  # снято
    assert (await _create(svc, player)).status_code == 201


async def test_expired_projection_entry_does_not_restrict(svc: MatchApp) -> None:
    player = svc.user()
    await sanction(svc, player, 1, ("online", 600))
    assert_problem(await _create(svc, player), 403, "match.online_banned")
    svc.clock.advance(seconds=601)
    assert (await _create(svc, player)).status_code == 201


async def test_chat_mute_comes_from_projection(svc: MatchApp) -> None:
    host, talker = svc.user(), svc.user()
    code = (await svc.create(host, LOBBY, collection_ids=[str(uuid7())]))["code"]
    await svc.join(code, talker)
    assert (await svc.post(talker, code, "match/chat", text="привет")).status_code == 200
    await sanction(svc, talker, 1, ("chat", None))
    assert_problem(await svc.post(talker, code, "match/chat", text="ещё"), 403, "match.chat_muted")
    await sanction(svc, talker, 2)
    assert (await svc.post(talker, code, "match/chat", text="снова")).status_code == 200


async def test_event_evicts_player_from_lobby_and_revokes_subscription(svc: MatchApp) -> None:
    host, guest = svc.user(), svc.user()
    code = (await svc.create(host, LOBBY, collection_ids=[str(uuid7())]))["code"]
    await svc.join(code, guest)
    assert await sanction(svc, guest, 1, ("online", None)) is Outcome.PROCESSED
    state = await svc.until(host, code, lambda s: len(s["room"]["players"]) == 1)
    assert state["room"]["players"][0]["user_id"] == str(host.user_id)
    assert (guest.user_id, f"room:{code}") in svc.subscriptions.revoked
    # Выбитый не может вернуться.
    assert_problem(await svc.post(guest, code, "join"), 403, "match.online_banned")


async def test_event_removes_player_from_queue(svc: MatchApp) -> None:
    await svc.ladder("music")
    player = svc.user()
    assert (await svc.enter(player)).status_code == 200
    queue = container_for(svc.infra).ranked_queue
    assert await queue.get(player.user_id) is not None
    await sanction(svc, player, 1, ("online", None))
    assert await queue.get(player.user_id) is None
    assert_problem(await svc.queue_tick(player), 403, "match.online_banned")


async def test_chat_only_sanction_does_not_evict(svc: MatchApp) -> None:
    host, guest = svc.user(), svc.user()
    code = (await svc.create(host, LOBBY, collection_ids=[str(uuid7())]))["code"]
    await svc.join(code, guest)
    await sanction(svc, guest, 1, ("chat", None))
    state = await svc.state(host, code)
    assert len(state["room"]["players"]) == 2


async def test_duplicate_and_stale_events_change_nothing(svc: MatchApp) -> None:
    host, guest = svc.user(), svc.user()
    code = (await svc.create(host, LOBBY, collection_ids=[str(uuid7())]))["code"]
    await sanction(svc, guest, 5)  # проекция: версия 5, санкций нет
    await svc.join(code, guest)
    # Старая версия с ONLINE (приехала позже) игрока не выбивает и проекцию не меняет.
    assert await sanction(svc, guest, 4, ("online", None)) is Outcome.PROCESSED
    assert len((await svc.state(host, code))["room"]["players"]) == 2
    assert (await svc.post(guest, code, "ready", ready=True)).status_code == 200
    async with svc.infra.sessions() as session:  # type: ignore[misc]
        version = await session.scalar(
            text("SELECT version FROM sanctions_proj WHERE user_id = :u"), {"u": guest.user_id}
        )
    assert version == 5


async def test_same_event_twice_is_duplicate(svc: MatchApp) -> None:
    player = svc.user()
    first = load_example("social.UserSanctionsChanged")
    envelope = first.model_copy(
        update={"payload": {**first.payload, "user_id": str(player.user_id)}}
    )
    assert await svc.deliver(envelope) is Outcome.PROCESSED
    assert await svc.deliver(envelope) is Outcome.DUPLICATE


@pytest.mark.parametrize(
    "broken",
    [
        {"version": 0},
        {"mask": 70000},
        {"user_id": "не-uuid"},
        {"active": [{"kind": "online"}]},
        {"next_expiry_at": "2026-10-31T12:09:00"},
    ],
)
async def test_broken_payload_goes_to_dlq(svc: MatchApp, broken: dict[str, Any]) -> None:
    base = load_example("social.UserSanctionsChanged")
    envelope = base.model_copy(update={"payload": {**base.payload, **broken}})
    assert await svc.deliver(envelope) is Outcome.DEAD


async def test_realtime_token_is_rechecked_on_renewal(svc: MatchApp) -> None:
    host = svc.user()
    code = (await svc.create(host, "music.duel.classic"))["code"]
    url = f"/v1/rooms/{code}/realtime-token"
    assert (await svc.client.post(url, headers=host.headers)).status_code == 200
    await sanction(svc, host, 1, ("online", None))
    assert_problem(await svc.client.post(url, headers=host.headers), 403, "match.online_banned")
