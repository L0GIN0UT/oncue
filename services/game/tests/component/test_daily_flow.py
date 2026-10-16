"""Полный дейлик: события → проекция → сиды режимов → наборы дня → игра → итог → события."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
import yaml

from gv_core.ids import uuid7
from gv_testkit import assert_event_matches_schema, assert_problem, assert_published

pytestmark = pytest.mark.component

MUSIC_DAILY = "music.daily.classic"
FILM_RANDOM_SEED = (
    Path(__file__).resolve().parents[4] / "seeds" / "game" / "modes" / "film.random.frame.yaml"
)
FORBIDDEN_BEFORE_REVEAL = {"media_item_id", "title", "asset_set_id", "persons"}


def keys_outside_reveal(value: Any) -> set[str]:
    """Все ключи JSON, кроме содержимого ``reveal`` (античит, §9 п. 1)."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, inner in value.items():
            found.add(key)
            if key != "reveal":
                found |= keys_outside_reveal(inner)
    elif isinstance(value, list):
        for inner in value:
            found |= keys_outside_reveal(inner)
    return found


def bearer(token: str, key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def ikey() -> str:
    return f"k-{uuid7().hex}"


async def prepared(game: Any, count: int = 15) -> list[UUID]:
    """Сиды режимов опубликованы, в проекции ``count`` играбельных треков, наборы дня есть."""
    assert (await game.run_job("game.modes.bootstrap")).value == "ok"
    items = [await game.add_item(ready_for=MUSIC_DAILY) for _ in range(count)]
    assert (await game.run_job("game.daily.schedule")).value == "ok"
    return items


async def start(game: Any, token: str, mode: str = MUSIC_DAILY, **extra: Any) -> httpx.Response:
    response: httpx.Response = await game.client.post(
        "/v1/sessions", json={"mode_key": mode, **extra}, headers=bearer(token, ikey())
    )
    return response


async def move(game: Any, token: str, sid: str, path: str, body: dict[str, Any]) -> httpx.Response:
    response: httpx.Response = await game.client.post(
        f"/v1/sessions/{sid}/{path}", json=body, headers=bearer(token, ikey())
    )
    return response


async def round_item(game: Any, sid: str, index: int) -> UUID:
    """Загаданная единица раунда (тест читает БД, клиент её не видит)."""
    assert game.infra.sessions is not None
    async with game.infra.sessions() as s:
        from game_service.infrastructure.db.repositories import SqlSessionRepository

        session = await SqlSessionRepository(s).get(UUID(sid))
    assert session is not None
    return session.rounds[index].media_item_id


async def play_out(game: Any, token: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Сдаться во всех оставшихся раундах."""
    sid = snapshot["id"]
    while snapshot["status"] == "active":
        index = snapshot["current_round"]
        r = await move(
            game, token, sid, f"rounds/{index}/give-up", {"expected_version": snapshot["version"]}
        )
        assert r.status_code == 200, r.text
        snapshot = r.json()["session"]
    return snapshot


async def test_guest_daily_full_cycle(game: Any) -> None:  # noqa: PLR0915 — сценарий §0 п. 2 целиком
    await prepared(game)
    modes = (await game.client.get("/v1/modes", params={"domain": "music"})).json()
    assert {m["key"] for m in modes} >= {MUSIC_DAILY, "music.random.classic"}
    assert all(m["domain"] == "music" for m in modes)
    daily_mode = next(m for m in modes if m["key"] == MUSIC_DAILY)
    assert daily_mode["levels"][0]["hint"] == {"seconds": 0.5}
    assert daily_mode["rounds"] == 5

    guest = game.issuer.issue_guest()
    created = await start(game, guest)
    assert created.status_code == 201, created.text
    assert created.headers["cache-control"] == "no-store"
    snap = created.json()
    sid = snap["id"]
    assert snap["day"] == "2026-10-10"
    assert snap["max_score"] == 30
    assert not keys_outside_reveal(snap) & FORBIDDEN_BEFORE_REVEAL

    # Ассет только для открытого уровня.
    clue = await game.client.post(
        f"/v1/sessions/{sid}/rounds/0/clue", json={"level": 1}, headers=bearer(guest)
    )
    assert clue.status_code == 200, clue.text
    assert clue.json()["url"].startswith("/media/a/")
    assert clue.json()["kind"] == "audio"
    assert clue.json()["meta"]["seconds"] == 0.5
    locked = await game.client.post(
        f"/v1/sessions/{sid}/rounds/0/clue", json={"level": 2}, headers=bearer(guest)
    )
    assert_problem(locked, 403, "game.level_locked")
    future = await game.client.post(
        f"/v1/sessions/{sid}/rounds/1/clue", json={"level": 1}, headers=bearer(guest)
    )
    assert_problem(future, 409, "game.round_not_active")

    # Неверный ответ → уровень 2; пропуск → уровень 3; верный выбор → ревил.
    wrong = await move(
        game,
        guest,
        sid,
        "guesses",
        {"round_index": 0, "expected_version": snap["version"], "answer": {"text": "совсем не то"}},
    )
    assert wrong.status_code == 200, wrong.text
    body = wrong.json()
    assert body["result"] == "miss" and body["round"]["level"] == 2
    assert not keys_outside_reveal(body) & FORBIDDEN_BEFORE_REVEAL
    skipped = await move(
        game,
        guest,
        sid,
        "skips",
        {"round_index": 0, "expected_version": body["session"]["version"]},
    )
    assert skipped.json()["round"]["level"] == 3
    answer = await round_item(game, sid, 0)
    right = await move(
        game,
        guest,
        sid,
        "guesses",
        {
            "round_index": 0,
            "expected_version": skipped.json()["session"]["version"],
            "answer": {"media_item_id": str(answer)},
        },
    )
    assert right.status_code == 200, right.text
    result = right.json()
    assert result["result"] == "exact"
    assert result["round"]["points"] == 4
    reveal = result["round"]["reveal"]
    assert reveal["media_item_id"] == str(answer)
    assert reveal["final_clue"]["url"].startswith("/media/a/")
    assert result["session"]["current_round"] == 1

    final = await play_out(game, guest, result["session"])
    assert final["status"] == "finished"
    assert final["score"] == 4

    res = await game.client.get(f"/v1/sessions/{sid}/result", headers=bearer(guest))
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["score"] == 4 and out["max_score"] == 30
    assert [r["outcome"] for r in out["rounds"]] == ["exact"] + ["gave_up"] * 4
    assert "4/30" in out["share"]["text"] and "https://oncue.test/" in out["share"]["text"]
    assert out["daily_stats"] is not None
    assert all(s["exact_pct"] == 0.0 for s in out["daily_stats"])  # гость не в статистике

    # Повторный вход — результат, переиграть нельзя.
    again = await start(game, guest)
    body = assert_problem(again, 409, "game.daily_already_played")
    assert body["details"]["session_id"] == sid

    status = await game.client.get("/v1/daily", params={"domain": "music"}, headers=bearer(guest))
    (daily,) = status.json()
    assert daily["status"] == "finished" and daily["session_id"] == sid
    assert daily["next_reset_at"] == "2026-10-10T21:00:00Z"

    events = await game.outbox()
    finished = assert_published(events, "game.GameFinished", {"session_id": sid})
    assert finished.payload["is_guest"] is True and finished.payload["user_id"] is None
    assert_event_matches_schema(finished)
    assert not [e for e in events if e.event_type == "game.DailyCompleted"]


async def test_user_daily_publishes_daily_completed_and_stats(game: Any) -> None:
    await prepared(game)
    user_id = uuid7()
    token = game.issuer.issue(user_id=user_id)
    snap = (await start(game, token)).json()
    answer = await round_item(game, snap["id"], 0)
    r = await move(
        game,
        token,
        snap["id"],
        "guesses",
        {
            "round_index": 0,
            "expected_version": snap["version"],
            "answer": {"media_item_id": str(answer)},
        },
    )
    assert r.json()["round"]["points"] == 6
    final = await play_out(game, token, r.json()["session"])
    assert final["score"] == 6

    events = await game.outbox()
    finished = assert_published(events, "game.GameFinished", {"session_id": snap["id"]})
    completed = assert_published(events, "game.DailyCompleted", {"session_id": snap["id"]})
    for e in (finished, completed):
        assert_event_matches_schema(e)
    assert completed.payload["user_id"] == str(user_id)
    assert completed.payload["exact_count"] == 1
    assert completed.payload["day"] == "2026-10-10"

    out = (await game.client.get(f"/v1/sessions/{snap['id']}/result", headers=bearer(token))).json()
    assert out["daily_stats"][0] == {"index": 0, "exact_pct": 100.0}


async def test_resume_active_daily_returns_200(game: Any) -> None:
    await prepared(game)
    token = game.issuer.issue()
    first = await start(game, token)
    second = await start(game, token)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]


async def test_foreign_session_is_404(game: Any) -> None:
    await prepared(game)
    owner, stranger = game.issuer.issue(), game.issuer.issue()
    sid = (await start(game, owner)).json()["id"]
    for response in (
        await game.client.get(f"/v1/sessions/{sid}", headers=bearer(stranger)),
        await game.client.post(
            f"/v1/sessions/{sid}/rounds/0/clue", json={"level": 1}, headers=bearer(stranger)
        ),
        await move(game, stranger, sid, "skips", {"round_index": 0, "expected_version": 1}),
        await game.client.get(f"/v1/sessions/{sid}/result", headers=bearer(stranger)),
        await game.client.get(f"/v1/sessions/{uuid7()}", headers=bearer(owner)),
    ):
        assert_problem(response, 404, "game.session_not_found")


async def test_desync_and_idempotent_replay(game: Any) -> None:
    await prepared(game)
    token = game.issuer.issue()
    snap = (await start(game, token)).json()
    sid = snap["id"]
    key = ikey()
    body = {"round_index": 0, "expected_version": snap["version"]}
    first = await game.client.post(
        f"/v1/sessions/{sid}/skips", json=body, headers=bearer(token, key)
    )
    replay = await game.client.post(
        f"/v1/sessions/{sid}/skips", json=body, headers=bearer(token, key)
    )
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    assert replay.json()["round"]["level"] == 2  # пропуск применён один раз

    stale = await move(game, token, sid, "skips", body)
    problem = assert_problem(stale, 409, "game.desync")
    # Контракт: details = {"session": SessionSnapshot} — тот же снимок, что в ответе хода.
    assert set(problem["details"]) == {"session"}
    assert problem["details"]["session"] == first.json()["session"]
    assert stale.headers["cache-control"] == "no-store"

    reused = await game.client.post(
        f"/v1/sessions/{sid}/skips",
        json={**body, "expected_version": 99},
        headers=bearer(token, key),
    )
    assert_problem(reused, 409, "idempotency.key_reused")


async def test_gone_asset_voids_round_and_lowers_max_score(game: Any) -> None:
    await prepared(game)
    token = game.issuer.issue()
    snap = (await start(game, token)).json()
    sid = snap["id"]
    assert game.infra.sessions is not None
    from game_service.infrastructure.db.repositories import SqlSessionRepository

    async with game.infra.sessions() as s:
        session = await SqlSessionRepository(s).get(UUID(sid))
    assert session is not None and session.rounds[0].asset is not None
    game.assets.gone.add(session.rounds[0].asset.asset_set_id)

    r = await game.client.post(
        f"/v1/sessions/{sid}/rounds/0/clue", json={"level": 1}, headers=bearer(token)
    )
    assert_problem(r, 410, "game.asset_unavailable")
    after = (await game.client.get(f"/v1/sessions/{sid}", headers=bearer(token))).json()
    assert after["rounds"][0]["status"] == "void"
    assert after["rounds"][0]["reveal"] is None
    assert after["max_score"] == 24
    assert after["current_round"] == 1


async def test_assets_unavailable_is_503(game: Any) -> None:
    await prepared(game)
    token = game.issuer.issue()
    sid = (await start(game, token)).json()["id"]
    game.assets.unavailable = True
    r = await game.client.post(
        f"/v1/sessions/{sid}/rounds/0/clue", json={"level": 1}, headers=bearer(token)
    )
    assert r.status_code == 503


async def test_daily_not_ready_without_pool(game: Any) -> None:
    assert (await game.run_job("game.modes.bootstrap")).value == "ok"
    r = await start(game, game.issuer.issue())
    assert_problem(r, 503, "game.daily_not_ready")


async def test_concurrent_daily_starts_create_one_session(game: Any) -> None:
    import asyncio

    await prepared(game)
    token = game.issuer.issue()
    responses = await asyncio.gather(*(start(game, token) for _ in range(4)))
    assert {r.status_code for r in responses} <= {200, 201}
    assert len({r.json()["id"] for r in responses}) == 1


async def test_abandon_job_and_user_deleted(game: Any) -> None:
    await prepared(game)
    user_id = uuid7()
    token = game.issuer.issue(user_id=user_id)
    sid = (await start(game, token)).json()["id"]
    game.clock.advance(hours=25)
    assert (await game.run_job("game.sessions.abandon")).value == "ok"
    snap = (await game.client.get(f"/v1/sessions/{sid}", headers=bearer(token))).json()
    assert snap["status"] == "abandoned"

    from gv_testkit import load_example

    deleted = load_example("identity.UserDeleted")
    deleted = deleted.model_copy(update={"payload": {**deleted.payload, "user_id": str(user_id)}})
    assert (await game.deliver(deleted)).value == "processed"
    r = await game.client.get(f"/v1/sessions/{sid}", headers=bearer(token))
    assert_problem(r, 404, "game.session_not_found")


async def test_catalog_event_projects_title_aliases(game: Any) -> None:
    from game_service.infrastructure.db.repositories import SqlProjectionRepository

    from gv_messaging import Outcome
    from gv_testkit import load_example

    base = load_example("catalog.MediaItemCreated")
    item_id = uuid7()
    aliases = [
        {"text": "Брат", "normalized": "брат", "lang": "ru", "kind": "title"},
        {"text": "Brother", "normalized": "brother", "lang": "en", "kind": "alt"},
        {"text": "Brat", "normalized": "brat", "lang": None, "kind": "translit"},
        {"text": "Б", "normalized": "б", "lang": None, "kind": "short"},
    ]
    payload = {**base.payload, "media_item_id": str(item_id), "aliases": aliases}
    envelope = base.model_copy(update={"aggregate_id": str(item_id), "payload": payload})
    assert await game.deliver(envelope) is Outcome.PROCESSED

    async with game.infra.sessions() as session:
        item = await SqlProjectionRepository(session).get_item(item_id)
    assert item is not None
    assert item.aliases == ("брат", "brother", "brat", "б")
    ref = item.to_ref()
    assert ref.title_aliases == ("брат", "brother")


async def test_frame_clue_meta_uses_kernel_hint_fields(game: Any) -> None:
    assert (await game.run_job("game.modes.bootstrap")).value == "ok"
    for _ in range(12):
        await game.add_item(domain="film", genres=("drama",), ready_for="film.random.frame")
    token = game.issuer.issue()
    created = await start(game, token, "film.random.frame")
    assert created.status_code == 201, created.text
    sid = created.json()["id"]
    clue = await game.client.post(
        f"/v1/sessions/{sid}/rounds/0/clue", json={"level": 1}, headers=bearer(token)
    )
    assert clue.status_code == 200, clue.text
    meta = {k: v for k, v in clue.json()["meta"].items() if v is not None}
    first = yaml.safe_load(FILM_RANDOM_SEED.read_text("utf-8"))["clue"]["params"]["levels"][0]
    assert meta == {"blur_px": first["blur_px"], "crop": first["crop"]}
