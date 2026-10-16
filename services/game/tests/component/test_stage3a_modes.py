"""Режимы этапа 3a на реальных плагинах ядра: марафон, «больше/меньше», сетка, ревил.

Проверяют поведение через публичный API: правила завершения, состав раунда без утечек
(рекурсивный обход JSON), рекорды и статистику только для пользователей, свежую ссылку
ревила, идемпотентность ходов ``{option_id}``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import UUID

import httpx
import pytest

from gv_core.ids import uuid7
from gv_testkit import assert_event_matches_schema, assert_problem, assert_published

pytestmark = pytest.mark.component

MARATHON = "film.marathon.director"
PAIR = "film.pair.rating"
BRACKET = "film.bracket.weekly"
RANDOM = "music.random.classic"
BLACKOUT = "film.random.blackout"


def bearer(token: str, key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def ikey() -> str:
    return f"k-{uuid7().hex}"


async def start(game: Any, token: str, mode: str, **extra: Any) -> httpx.Response:
    response: httpx.Response = await game.client.post(
        "/v1/sessions", json={"mode_key": mode, **extra}, headers=bearer(token, ikey())
    )
    return response


async def guess(
    game: Any, token: str, snap: dict[str, Any], answer: dict[str, Any], key: str | None = None
) -> httpx.Response:
    body = {
        "round_index": snap["current_round"],
        "expected_version": snap["version"],
        "answer": answer,
    }
    response: httpx.Response = await game.client.post(
        f"/v1/sessions/{snap['id']}/guesses", json=body, headers=bearer(token, key or ikey())
    )
    return response


async def give_up(game: Any, token: str, snap: dict[str, Any]) -> httpx.Response:
    response: httpx.Response = await game.client.post(
        f"/v1/sessions/{snap['id']}/rounds/{snap['current_round']}/give-up",
        json={"expected_version": snap["version"]},
        headers=bearer(token, ikey()),
    )
    return response


async def stored(game: Any, sid: str) -> Any:
    """Сессия из БД (тест знает ответы, клиент — нет)."""
    from game_service.infrastructure.db.repositories import SqlSessionRepository

    assert game.infra.sessions is not None
    async with game.infra.sessions() as s:
        session = await SqlSessionRepository(s).get(UUID(sid))
    assert session is not None
    return session


def scalars_outside_reveal(value: Any) -> Iterator[Any]:
    """Все ключи и значения JSON, кроме содержимого ``reveal`` (античит, §11 п. 1)."""
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            if key != "reveal":
                yield from scalars_outside_reveal(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from scalars_outside_reveal(inner)
    else:
        yield value


def assert_hidden(payload: Any, *secrets: Any) -> None:
    found = set(map(str, scalars_outside_reveal(payload)))
    leaked = {str(s) for s in secrets} & found
    assert not leaked, f"утечка до ревила: {leaked}"


async def bootstrap(game: Any) -> None:
    assert (await game.run_job("game.modes.bootstrap")).value == "ok"


# ============================================================== марафон
async def test_marathon_by_director_ends_by_lives_and_keeps_record(game: Any) -> None:
    await bootstrap(game)
    nolan, other = uuid7(), uuid7()
    films = [
        await game.add_item(
            domain="film",
            year=2000 + i,
            persons=[(nolan, "Кристофер Нолан", "director")],
            ready_for=MARATHON,
        )
        for i in range(8)
    ]
    for _ in range(2):
        await game.add_item(
            domain="film", persons=[(other, "Другой Режиссёр", "director")], ready_for=MARATHON
        )
    user = uuid7()
    token = game.issuer.issue(user_id=user)

    modes = (await game.client.get("/v1/modes", params={"domain": "film"})).json()
    mode = next(m for m in modes if m["key"] == MARATHON)
    assert mode["start"] == {"subject": {"roles": ["director"], "max": 3}}
    assert (mode["answer_format"], mode["presentation"]) == ("classic", "score")

    hits = await game.client.get(
        f"/v1/modes/{MARATHON}/subjects", params={"q": "нолан"}, headers=bearer(token)
    )
    assert hits.status_code == 200, hits.text
    assert hits.json() == [
        {"subject_id": str(nolan), "name": "Кристофер Нолан", "role": "director", "items": 8}
    ]
    few = await game.client.get(
        f"/v1/modes/{MARATHON}/subjects", params={"q": "другой"}, headers=bearer(token)
    )
    assert few.json() == []  # у персоны меньше min_items играбельных

    assert_problem(await start(game, token, MARATHON), 422, "game.subject_required")
    created = await start(game, token, MARATHON, subject_ids=[str(nolan)])
    assert created.status_code == 201, created.text
    snap = created.json()
    assert len(snap["rounds"]) == 8 and snap["max_score"] == 48
    assert [r["status"] for r in snap["rounds"][:2]] == ["active", "pending"]
    assert snap["rounds"][0]["progress"] == {"lives_left": 3, "streak": 0, "stage": None}
    assert snap["rounds"][1]["progress"] is None and snap["rounds"][1]["shown"] == []
    session = await stored(game, snap["id"])
    assert {r.target for r in session.plan} | {session.rounds[0].media_item_id} == set(films)
    assert_hidden(snap, *films)

    answer = session.rounds[0].media_item_id
    r = await guess(game, token, snap, {"media_item_id": str(answer)})
    assert r.status_code == 200, r.text
    snap = r.json()["session"]
    assert snap["rounds"][1]["progress"] == {"lives_left": 3, "streak": 1, "stage": None}
    for lives in (2, 1, 0):
        r = await give_up(game, token, snap)
        assert r.status_code == 200, r.text
        snap = r.json()["session"]
        if lives:
            assert snap["rounds"][snap["current_round"]]["progress"]["lives_left"] == lives
    assert snap["status"] == "finished"
    assert len(snap["rounds"]) == 4 and snap["max_score"] == 24  # план отброшен

    result = (
        await game.client.get(f"/v1/sessions/{snap['id']}/result", headers=bearer(token))
    ).json()
    assert result["presentation"] == "score" and result["score"] == 6
    assert result["record"] == {"best": 6, "is_new": True, "percentile": 0.0}
    records = (await game.client.get("/v1/me/records", headers=bearer(token))).json()
    assert [(x["mode_key"], x["best"], x["percentile"]) for x in records] == [(MARATHON, 6, 0.0)]

    events = await game.outbox()
    finished = assert_published(events, "game.GameFinished", {"session_id": snap["id"]})
    assert_event_matches_schema(finished)
    assert len(finished.payload["rounds"]) == 4


async def test_subjects_rejected_where_mode_has_none(game: Any) -> None:
    await bootstrap(game)
    token = game.issuer.issue()
    r = await game.client.get(f"/v1/modes/{RANDOM}/subjects", headers=bearer(token))
    assert_problem(r, 422, "game.subjects_not_supported")
    r = await start(game, token, RANDOM, subject_ids=[str(uuid7())])
    assert_problem(r, 422, "game.subjects_not_supported")
    r = await start(game, token, MARATHON, subject_ids=[str(uuid7()) for _ in range(4)])
    assert_problem(r, 422, "game.too_many_subjects")


# ============================================================== «больше/меньше»
async def pair_pool(game: Any) -> dict[UUID, float]:
    await bootstrap(game)
    ratings = [5.1, 6.2, 7.3, 8.4, 9.5, 4.6]
    items = {}
    for value in ratings:
        item = await game.add_item(domain="film", metrics={"vote_average": value}, ready_for=PAIR)
        items[item] = value
    # Без метрики единица в пару не попадает (has_metric).
    await game.add_item(domain="film", metrics={}, ready_for=PAIR)
    return items


async def test_pair_hides_target_metric_until_answer(game: Any) -> None:
    items = await pair_pool(game)
    guest = game.issuer.issue_guest()
    created = await start(game, guest, PAIR)
    assert created.status_code == 201, created.text
    snap = created.json()
    session = await stored(game, snap["id"])
    first = session.rounds[0]
    anchor, target = first.shown[0].media_item_id, first.media_item_id
    round0 = snap["rounds"][0]
    assert [o["option_id"] for o in round0["options"]] == ["higher", "lower"]
    assert all(o["title"] is None for o in round0["options"])
    (shown,) = round0["shown"]
    assert shown["metric"] == items[anchor] and shown["option_id"] is None
    assert shown["clue"]["url"].startswith("/media/a/")
    planned = [p.target for p in session.plan]
    assert_hidden(snap, target, items[target], *planned, *(items[p] for p in planned))
    assert all(r["shown"] == [] for r in snap["rounds"][1:])

    assert_problem(
        await guess(game, guest, snap, {"media_item_id": str(target)}),
        422,
        "game.answer_kind_unsupported",
    )
    assert_problem(
        await guess(game, guest, snap, {"option_id": "sideways"}), 422, "game.option_unknown"
    )

    right = "higher" if items[target] > items[anchor] else "lower"
    key = ikey()
    first_try = await guess(game, guest, snap, {"option_id": right}, key)
    replay = await guess(game, guest, snap, {"option_id": right}, key)
    assert first_try.status_code == replay.status_code == 200, first_try.text
    assert first_try.json() == replay.json()  # ход применён один раз
    body = first_try.json()
    assert body["result"] == "exact" and body["stats"] is None
    assert body["round"]["reveal"]["metric"] == items[target]
    assert body["round"]["guesses"] == [
        {"kind": "option", "display": f"game.options.{right}", "result": "exact"}
    ]
    snap = body["session"]
    assert snap["version"] == created.json()["version"] + 1
    nxt = snap["rounds"][1]
    assert nxt["status"] == "active" and nxt["shown"][0]["metric"] == items[target]
    assert nxt["progress"]["streak"] == 1

    session = await stored(game, snap["id"])
    second = session.rounds[1]
    wrong = "lower" if items[second.media_item_id] > items[target] else "higher"
    lost = await guess(game, guest, snap, {"option_id": wrong})
    assert lost.json()["result"] == "miss"
    final = lost.json()["session"]
    assert final["status"] == "finished" and final["score"] == 1 and len(final["rounds"]) == 2

    result = (
        await game.client.get(f"/v1/sessions/{final['id']}/result", headers=bearer(guest))
    ).json()
    assert result["presentation"] == "streak" and result["score"] == 1
    assert result["record"] is None  # гость без рекордов
    assert (await game.client.get("/v1/me/records", headers=bearer(guest))).json() == []


async def test_pair_user_record_and_percentile(game: Any) -> None:
    await pair_pool(game)
    tokens = [game.issuer.issue(user_id=uuid7()) for _ in range(2)]
    scores = []
    for token in tokens:
        snap = (await start(game, token, PAIR)).json()
        streak = 0
        while snap["status"] == "active":
            session = await stored(game, snap["id"])
            r = session.rounds[snap["current_round"]]
            anchor = await metric_of(game, r.shown[0].media_item_id)
            value = await metric_of(game, r.media_item_id)
            # Первый игрок угадывает всё, второй ошибается сразу.
            right = "higher" if value > anchor else "lower"
            wrong = "lower" if right == "higher" else "higher"
            choice = right if token is tokens[0] else wrong
            snap = (await guess(game, token, snap, {"option_id": choice})).json()["session"]
            streak += int(choice == right)
        scores.append(streak)
    best = (await game.client.get("/v1/me/records", headers=bearer(tokens[0]))).json()
    worst = (await game.client.get("/v1/me/records", headers=bearer(tokens[1]))).json()
    assert best[0]["best"] == scores[0] and scores[0] >= 1
    assert best[0]["percentile"] == 50.0 and worst[0]["percentile"] == 0.0
    # IDOR: рекорды — только свои.
    assert [x["best"] for x in worst] == [0]


async def metric_of(game: Any, item_id: UUID) -> float:
    from game_service.infrastructure.db.repositories import SqlProjectionRepository

    async with game.infra.sessions() as s:
        item = await SqlProjectionRepository(s).get_item(item_id)
    assert item is not None
    value = item.metric("vote_average")
    assert value is not None
    return value


# ============================================================== сетка
async def bracket_pool(game: Any, count: int = 32) -> UUID:
    await bootstrap(game)
    director = uuid7()
    for i in range(count):
        await game.add_item(
            domain="film",
            title=f"Фильм сетки {i:02d}",
            persons=[(director, "Режиссёр Сетки", "director")],
            ready_for=BRACKET,
        )
    assert (await game.run_job("game.daily.schedule")).value == "ok"
    return director


async def play_bracket(game: Any, token: str, pick: str = "a") -> tuple[dict[str, Any], list[Any]]:
    created = await start(game, token, BRACKET)
    assert created.status_code == 201, created.text
    snap = created.json()
    stats = []
    while snap["status"] == "active":
        r = await guess(game, token, snap, {"option_id": pick})
        assert r.status_code == 200, r.text
        stats.append(r.json()["stats"])
        snap = r.json()["session"]
    return snap, stats


async def test_bracket_31_picks_result_and_reentry(game: Any) -> None:
    director = await bracket_pool(game)
    user = game.issuer.issue(user_id=uuid7())
    created = await start(game, user, BRACKET)
    snap = created.json()
    assert snap["day"] == "2026-10-05" and len(snap["rounds"]) == 31
    first = snap["rounds"][0]
    assert [o["option_id"] for o in first["options"]] == ["a", "b"]
    assert all(o["title"].startswith("Фильм сетки") for o in first["options"])
    assert [s["option_id"] for s in first["shown"]] == ["a", "b"]
    assert first["progress"]["stage"] == 32
    session = await stored(game, snap["id"])
    assert_hidden(snap, *(p.target for p in session.plan))

    stats = []
    while snap["status"] == "active":
        r = await guess(game, user, snap, {"option_id": "a"})
        assert r.status_code == 200, r.text
        stats.append(r.json()["stats"])
        snap = r.json()["session"]
        if snap["status"] == "active":
            stage = snap["rounds"][snap["current_round"]]["progress"]["stage"]
            assert stage in {32, 16, 8, 4, 2}
    assert len(stats) == 31 and all(s == {"agree_pct": 100.0} for s in stats)
    assert snap["score"] == 0 and snap["max_score"] == 0

    result = (
        await game.client.get(f"/v1/sessions/{snap['id']}/result", headers=bearer(user))
    ).json()
    assert result["presentation"] == "bracket" and result["daily_stats"] is None
    assert len(result["bracket"]["picks"]) == 31
    assert [p["stage"] for p in result["bracket"]["picks"]][-3:] == [4, 4, 2]
    assert result["bracket"]["champion"] == result["bracket"]["picks"][-1]["winner"]

    again = await start(game, user, BRACKET)
    body = assert_problem(again, 409, "game.daily_already_played")
    assert body["details"]["session_id"] == snap["id"]

    events = await game.outbox()
    completed = assert_published(events, "game.BracketCompleted", {"session_id": snap["id"]})
    assert_event_matches_schema(completed)
    assert completed.payload["period_start"] == "2026-10-05"
    assert len(completed.payload["picks"]) == 31
    assert not [
        e
        for e in events
        if e.event_type in {"game.GameFinished", "game.DailyCompleted"}
        and e.payload.get("session_id") == snap["id"]
    ]

    status = await game.client.get("/v1/daily", params={"domain": "film"}, headers=bearer(user))
    weekly = next(d for d in status.json() if d["mode_key"] == BRACKET)
    assert weekly["status"] == "finished" and weekly["day"] == "2026-10-05"
    assert weekly["next_reset_at"] == "2026-10-11T21:00:00Z"
    assert director


async def test_bracket_agree_pct_counts_only_users(game: Any) -> None:
    await bracket_pool(game)
    _, first = await play_bracket(game, game.issuer.issue(user_id=uuid7()), "a")
    _, guest = await play_bracket(game, game.issuer.issue_guest(), "b")
    _, second = await play_bracket(game, game.issuer.issue(user_id=uuid7()), "b")
    assert first[0] == {"agree_pct": 100.0}
    # Гость видит долю без своего голоса: пару (b над a) ещё никто из пользователей не выбрал.
    assert guest[0] == {"agree_pct": 0.0}
    assert second[0] == {"agree_pct": 50.0}


async def test_weekly_set_curation_and_theme(game: Any) -> None:
    director = await bracket_pool(game, 33)
    small = uuid7()
    await game.add_item(domain="film", persons=[(small, "Мало фильмов", "director")])
    headers = game.admin("game.daily.view", "game.daily.curate")
    sets = await game.client.get(
        "/admin/v1/daily-sets",
        params={"mode_key": BRACKET, "from": "2026-10-05", "to": "2026-10-12"},
        headers=headers,
    )
    assert sets.status_code == 200, sets.text
    current, ahead = sets.json()
    assert (current["day"], ahead["day"]) == ("2026-10-05", "2026-10-12")
    assert {current["period"], ahead["period"]} == {"week"}
    assert current["subject_id"] == str(director) and len(current["items"]) == 32
    assert current["mode_version"] == 1

    locked = await game.client.put(
        f"/admin/v1/daily-sets/{BRACKET}/2026-10-05/items/0",
        json={"media_item_id": ahead["items"][0]["media_item_id"], "reason": "замена кадра"},
        headers={**headers, "Idempotency-Key": ikey()},
    )
    assert_problem(locked, 409, "game.daily_locked")
    bad_theme = await game.client.post(
        f"/admin/v1/daily-sets/{BRACKET}/2026-10-12/regenerate",
        json={"reason": "другая тема", "subject_id": str(small)},
        headers={**headers, "Idempotency-Key": ikey()},
    )
    assert_problem(bad_theme, 409, "game.theme_not_playable")
    regen = await game.client.post(
        f"/admin/v1/daily-sets/{BRACKET}/2026-10-12/regenerate",
        json={"reason": "та же тема заново", "subject_id": str(director)},
        headers={**headers, "Idempotency-Key": ikey()},
    )
    assert regen.status_code == 200, regen.text
    assert regen.json()["subject_id"] == str(director) and regen.json()["status"] == "scheduled"


# ============================================================== ревил (D13)
async def test_final_clue_after_two_minutes_and_idor(game: Any) -> None:
    """Регресс хвоста этапа 2: ревил, открытый через 2 минуты, получает свежую ссылку."""
    await bootstrap(game)
    for _ in range(25):
        await game.add_item(ready_for=RANDOM)
    owner, stranger = game.issuer.issue(), game.issuer.issue()
    snap = (await start(game, owner, RANDOM)).json()
    sid = snap["id"]
    url = f"/v1/sessions/{sid}/rounds/0/final-clue"
    assert_problem(
        await game.client.post(url, headers=bearer(owner)), 409, "game.round_not_finished"
    )
    done = (await give_up(game, owner, snap)).json()
    old = done["round"]["reveal"]["final_clue"]

    game.clock.advance(seconds=120)
    fresh = await game.client.post(url, headers=bearer(owner))
    assert fresh.status_code == 200, fresh.text
    clue = fresh.json()
    assert clue["url"] != old["url"] and clue["kind"] == "audio"
    assert clue["expires_at"] > game.clock.now().isoformat()
    again = (await game.client.get(f"/v1/sessions/{sid}", headers=bearer(owner))).json()
    assert again["rounds"][0]["reveal"]["final_clue"]["url"] == clue["url"]

    assert_problem(
        await game.client.post(url, headers=bearer(stranger)), 404, "game.session_not_found"
    )
    assert_problem(
        await game.client.post(f"/v1/sessions/{sid}/rounds/3/final-clue", headers=bearer(owner)),
        409,
        "game.round_not_finished",
    )

    session = await stored(game, sid)
    game.assets.gone.add(session.rounds[1].asset.asset_set_id)
    void = await game.client.post(
        f"/v1/sessions/{sid}/rounds/1/clue", json={"level": 1}, headers=bearer(owner)
    )
    assert_problem(void, 410, "game.asset_unavailable")
    assert_problem(
        await game.client.post(f"/v1/sessions/{sid}/rounds/1/final-clue", headers=bearer(owner)),
        410,
        "game.asset_unavailable",
    )


async def test_blackout_needs_both_asset_sets_and_reveals_frame(game: Any) -> None:
    await bootstrap(game)
    for _ in range(12):
        await game.add_item(domain="film", ready_for=BLACKOUT)  # только звук, без кадра
    token = game.issuer.issue()
    assert_problem(await start(game, token, BLACKOUT), 503, "game.pool_empty")
    for _ in range(6):
        await game.add_item(domain="film", ready_for=BLACKOUT, reveal=True)
    snap = (await start(game, token, BLACKOUT)).json()
    clue = await game.client.post(
        f"/v1/sessions/{snap['id']}/rounds/0/clue", json={"level": 1}, headers=bearer(token)
    )
    assert clue.json()["kind"] == "audio"
    done = (await give_up(game, token, snap)).json()
    assert done["round"]["reveal"]["final_clue"]["kind"] == "image"
