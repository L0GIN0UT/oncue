"""Игра по сборнику (этап 3b, ADR 0028, 0029) через публичный API на реальных плагинах.

Проекция сборника строится из событий ``collections.collection.v1``, ассеты — из
``AssetsReady`` (каталожные и со ``scope`` сборника). Проверяется: старт и ошибки старта,
число раундов, античит снимка (рекурсивный обход), итог и ``GameFinished`` с полями
сборника, доигрывание скрытого сборника, свои эмодзи автора, режимы сборника и
играбельность черновика, а также что каталожные режимы наборов сборника не видят.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
import yaml
from game_service.infrastructure.db.repositories import SqlSessionRepository
from game_service.infrastructure.db.tables import clue_assets
from sqlalchemy import func, select

from gv_core.ids import uuid7
from gv_game_kernel import params_hash
from gv_messaging import Outcome
from gv_testkit import assert_event_matches_schema, assert_problem, assert_published, load_example

pytestmark = pytest.mark.component

FRAME = "film.collection.frame"
EMOJI = "film.collection.emoji"
INTRO = "music.collection.intro"
RANDOM_EMOJI = "film.random.emoji"
RANDOM = "music.random.classic"
BRACKET = "film.bracket.weekly"
SEEDS_DIR = Path(__file__).resolve().parents[4] / "seeds" / "game" / "modes"


def bearer(token: str, key: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


async def start(game: Any, token: str, mode: str, **extra: Any) -> httpx.Response:
    response: httpx.Response = await game.client.post(
        "/v1/sessions",
        json={"mode_key": mode, **extra},
        headers=bearer(token, f"k-{uuid7().hex}"),
    )
    return response


async def give_up(game: Any, token: str, snap: dict[str, Any]) -> httpx.Response:
    response: httpx.Response = await game.client.post(
        f"/v1/sessions/{snap['id']}/rounds/{snap['current_round']}/give-up",
        json={"expected_version": snap["version"]},
        headers=bearer(token, f"k-{uuid7().hex}"),
    )
    return response


async def stored(game: Any, sid: str) -> Any:
    """Сессия из БД (тест знает ответы, клиент — нет)."""
    async with game.infra.sessions() as s:
        session = await SqlSessionRepository(s).get(UUID(sid))
    assert session is not None
    return session


def _scalars(value: Any) -> Iterator[Any]:
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from _scalars(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _scalars(inner)
    else:
        yield value


def assert_hidden(payload: Any, *secrets: Any) -> None:
    """Античит: ни ключ, ни значение JSON (на любой глубине) не раскрывает секреты."""
    leaked = {str(s) for s in secrets} & set(map(str, _scalars(payload)))
    assert not leaked, f"утечка до ревила: {leaked}"


async def bootstrap(game: Any) -> None:
    assert (await game.run_job("game.modes.bootstrap")).value == "ok"


async def publish(
    game: Any,
    items: list[UUID],
    *,
    custom: frozenset[UUID] = frozenset(),
    cid: UUID | None = None,
    domain: str = "film",
    status: str = "published",
    version: int = 1,
    content_version: int = 1,
    event: str = "collections.CollectionPublished",
) -> UUID:
    """Событие сборника (снимок состава) в consumer game."""
    cid = cid or uuid7()
    base = load_example(event)
    payload = {
        **base.payload,
        "collection_id": str(cid),
        "version": version,
        "content_version": content_version,
        "domain": domain,
        "status": status,
        "default_mode_key": f"{domain}.collection.{'frame' if domain == 'film' else 'intro'}",
        "items": [
            {
                "media_item_id": str(item),
                "position": pos,
                **({"custom": {"emoji": "🎬🍿🎥🎭🌟🎻"}} if item in custom else {}),
            }
            for pos, item in enumerate(items)
        ],
    }
    envelope = base.model_copy(update={"aggregate_id": str(cid), "payload": payload})
    assert await game.deliver(envelope) is Outcome.PROCESSED
    return cid


async def delete(game: Any, cid: UUID, version: int) -> None:
    base = load_example("collections.CollectionDeleted")
    payload = {**base.payload, "collection_id": str(cid), "version": version}
    envelope = base.model_copy(update={"aggregate_id": str(cid), "payload": payload})
    assert await game.deliver(envelope) is Outcome.PROCESSED


async def ready_scoped(game: Any, item: UUID, mode: str, cid: UUID, *, version: int) -> UUID:
    """``AssetsReady`` набора сборника (свои эмодзи автора) под ``clue.params`` режима."""
    clue = yaml.safe_load((SEEDS_DIR / f"{mode}.yaml").read_text("utf-8"))["clue"]
    base = load_example("media_pipeline.AssetsReady", variant="collection")
    asset_set_id = uuid7()
    payload = {
        **base.payload,
        "asset_set_id": str(asset_set_id),
        "media_item_id": str(item),
        "clue_type": clue["type"],
        "params_hash": params_hash(clue["params"]),
        "levels_count": len(clue["params"]["levels"]),
        "version": version,
        "scope": {"kind": "collection", "id": str(cid)},
    }
    envelope = base.model_copy(update={"aggregate_id": str(item), "payload": payload})
    assert await game.deliver(envelope) is Outcome.PROCESSED
    return asset_set_id


async def scoped_rows(game: Any, cid: UUID) -> int:
    async with game.infra.sessions() as s:
        value = await s.scalar(
            select(func.count()).select_from(clue_assets).where(clue_assets.c.scope_id == cid)
        )
    return int(value or 0)


async def films(game: Any, count: int, mode: str | None = FRAME) -> list[UUID]:
    return [await game.add_item(domain="film", ready_for=mode) for _ in range(count)]


async def modes_of(game: Any, cid: UUID) -> dict[str, dict[str, Any]]:
    response = await game.client.get(f"/v1/collections/{cid}/modes")
    assert response.status_code == 200, response.text
    return {m["mode_key"]: m for m in response.json()}


async def finish(game: Any, token: str, snap: dict[str, Any]) -> dict[str, Any]:
    while snap["status"] == "active":
        r = await give_up(game, token, snap)
        assert r.status_code == 200, r.text
        snap = r.json()["session"]
    return snap


# ============================================================== старт и итог
async def test_collection_session_plays_and_reports_collection(game: Any) -> None:
    await bootstrap(game)
    playable = await films(game, 6)
    unready = await game.add_item(domain="film")
    cid = await publish(game, [*playable, unready], version=3, content_version=2)

    modes = await modes_of(game, cid)
    assert set(modes) == {FRAME, EMOJI}  # домен сборника, только source collection
    assert modes[FRAME] == {
        "mode_key": FRAME,
        "title_key": "modes.film.collection.frame.title",
        "clue_type": "frame_progressive",
        "allow_guest": True,
        "playable": 6,
        "total": 7,
        "available": True,
        "round_options": [5, "all"],  # 10 и 25 при шести играбельных старт не примет
    }
    assert (modes[EMOJI]["playable"], modes[EMOJI]["available"]) == (0, False)
    assert modes[EMOJI]["round_options"] == []
    listed = (await game.client.get("/v1/modes", params={"domain": "film"})).json()
    assert not {m["key"] for m in listed} & {FRAME, EMOJI}

    user = uuid7()
    token = game.issuer.issue(user_id=user)
    created = await start(game, token, FRAME, collection_id=str(cid))  # rounds — первый вариант
    assert created.status_code == 201, created.text
    snap = created.json()
    assert len(snap["rounds"]) == 5 and snap["max_score"] == 30
    assert [r["status"] for r in snap["rounds"]] == ["active"] + ["pending"] * 4
    assert_hidden(snap, *playable, unready)
    session = await stored(game, snap["id"])
    assert (session.collection_id, session.collection_version) == (cid, 2)
    targets = [session.rounds[0].media_item_id, *(p.target for p in session.plan)]
    assert len(set(targets)) == 5 and set(targets) <= set(playable)

    snap = await finish(game, token, snap)
    assert snap["status"] == "finished"
    result = (
        await game.client.get(f"/v1/sessions/{snap['id']}/result", headers=bearer(token))
    ).json()
    assert result["collection"] == {"id": str(cid), "version": 2}
    assert result["record"] is None  # features.records выключен у шаблонов сборника
    finished = assert_published(
        await game.outbox(), "game.GameFinished", {"session_id": snap["id"]}
    )
    assert_event_matches_schema(finished)
    assert finished.payload["collection_id"] == str(cid)
    assert finished.payload["collection_version"] == 2
    assert finished.payload["user_id"] == str(user)


async def test_rounds_choice_follows_round_options_and_pool(game: Any) -> None:
    await bootstrap(game)
    cid = await publish(game, await films(game, 6))
    token = game.issuer.issue()
    r = await start(game, token, FRAME, collection_id=str(cid), rounds="all")
    assert r.status_code == 201, r.text
    assert len(r.json()["rounds"]) == 6
    problem = assert_problem(
        await start(game, game.issuer.issue(), FRAME, collection_id=str(cid), rounds=10),
        422,
        "game.pool_too_small",
    )
    assert problem["details"] == {"available": 6, "required": 10}
    r = await start(game, game.issuer.issue(), FRAME, collection_id=str(cid), rounds=7)
    assert r.status_code == 422  # вне {5, 10, 25, "all"} — схема запроса

    small = await publish(game, await films(game, 4))
    assert_problem(
        await start(game, game.issuer.issue(), FRAME, collection_id=str(small), rounds="all"),
        422,
        "game.pool_too_small",
    )
    assert (await modes_of(game, small))[FRAME]["available"] is False


async def test_start_errors(game: Any) -> None:
    await bootstrap(game)
    token = game.issuer.issue()
    film = await publish(game, await films(game, 5))
    music = await publish(
        game,
        [await game.add_item(domain="music", ready_for=INTRO) for _ in range(5)],
        domain="music",
    )
    assert_problem(await start(game, token, FRAME), 422, "game.collection_required")
    assert_problem(
        await start(game, token, FRAME, collection_id=str(uuid7())),
        404,
        "game.collection_not_found",
    )
    assert_problem(
        await start(game, token, FRAME, collection_id=str(music)),
        422,
        "game.collection_domain_mismatch",
    )
    assert_problem(
        await start(game, token, RANDOM_EMOJI, collection_id=str(film)),
        422,
        "game.collection_not_supported",
    )
    problem = assert_problem(
        await start(game, token, RANDOM, rounds=5), 422, "game.rounds_not_allowed"
    )
    assert problem["details"] == {"requested": 5, "options": []}
    assert_problem(
        await game.client.get(f"/v1/collections/{uuid7()}/modes"), 404, "game.collection_not_found"
    )


async def test_hidden_or_deleted_collection_cannot_start_but_started_session_finishes(
    game: Any,
) -> None:
    await bootstrap(game)
    items = await films(game, 5)
    cid = await publish(game, items)
    token = game.issuer.issue_guest()  # allow_guest у шаблона
    created = await start(game, token, FRAME, collection_id=str(cid))
    assert created.status_code == 201, created.text

    await publish(
        game, items, cid=cid, status="hidden", version=2, event="collections.CollectionUpdated"
    )
    assert_problem(
        await start(game, game.issuer.issue(), FRAME, collection_id=str(cid)),
        404,
        "game.collection_not_found",
    )
    assert_problem(
        await game.client.get(f"/v1/collections/{cid}/modes"), 404, "game.collection_not_found"
    )
    snap = await finish(game, token, created.json())
    assert snap["status"] == "finished"
    assert [r["status"] for r in snap["rounds"]] == ["finished"] * 5  # раунды не аннулированы
    finished = assert_published(
        await game.outbox(), "game.GameFinished", {"session_id": snap["id"]}
    )
    assert finished.payload["collection_id"] == str(cid) and finished.payload["user_id"] is None

    # Опоздавший снимок не воскрешает удалённый сборник.
    await delete(game, cid, version=3)
    await publish(game, items, cid=cid, version=2)
    assert_problem(
        await start(game, game.issuer.issue(), FRAME, collection_id=str(cid)),
        404,
        "game.collection_not_found",
    )


# ============================================================== свои эмодзи автора
async def test_own_emoji_sets_play_first_and_are_dropped_with_snapshot(game: Any) -> None:
    await bootstrap(game)
    catalog = await films(game, 4, EMOJI)
    own_only = await game.add_item(domain="film")  # каталожного набора emoji нет
    both = await game.add_item(domain="film", ready_for=EMOJI)
    items = [*catalog, own_only, both]
    cid = await publish(game, items, custom=frozenset({own_only, both}))
    own_set = await ready_scoped(game, own_only, EMOJI, cid, version=2)
    both_set = await ready_scoped(game, both, EMOJI, cid, version=2)

    assert (await modes_of(game, cid))[EMOJI]["playable"] == 6
    token = game.issuer.issue()
    created = await start(game, token, EMOJI, collection_id=str(cid), rounds="all")
    assert created.status_code == 201, created.text
    snap = created.json()
    assert_hidden(snap, *items, own_set, both_set)
    while snap["status"] == "active":
        snap = (await give_up(game, token, snap)).json()["session"]
    session = await stored(game, snap["id"])
    by_item = {r.media_item_id: r.asset.asset_set_id for r in session.rounds}
    assert by_item[own_only] == own_set and by_item[both] == both_set

    # Каталожный режим своих наборов сборника не видит.
    other = game.issuer.issue()
    random_snap = (await start(game, other, RANDOM_EMOJI)).json()
    random_session = await stored(game, random_snap["id"])
    random_targets = {
        random_session.rounds[0].media_item_id,
        *(p.target for p in random_session.plan),
    }
    assert own_only not in random_targets
    assert {r.asset.asset_set_id for r in random_session.rounds if r.asset} & {
        own_set,
        both_set,
    } == set()

    # Эмодзи у own_only убрали: его свой набор удалён, у both — остался.
    await publish(
        game,
        items,
        cid=cid,
        custom=frozenset({both}),
        version=2,
        content_version=2,
        event="collections.CollectionUpdated",
    )
    assert await scoped_rows(game, cid) == 1
    assert (await modes_of(game, cid))[EMOJI]["playable"] == 5
    # Скрытие убирает все свои наборы; набор, собранный до скрытия, не принимается.
    await publish(
        game, items, cid=cid, status="hidden", version=3, event="collections.CollectionUpdated"
    )
    assert await scoped_rows(game, cid) == 0
    await ready_scoped(game, both, EMOJI, cid, version=3)
    assert await scoped_rows(game, cid) == 0


async def test_deleted_collection_drops_own_sets(game: Any) -> None:
    await bootstrap(game)
    items = await films(game, 5, EMOJI)
    cid = await publish(game, items, custom=frozenset(items[:2]))
    for item in items[:2]:
        await ready_scoped(game, item, EMOJI, cid, version=2)
    assert await scoped_rows(game, cid) == 2
    await delete(game, cid, version=2)
    assert await scoped_rows(game, cid) == 0


# ============================================================== каталог шаблонов сборника
async def test_modes_kind_splits_play_modes_and_collection_templates(game: Any) -> None:
    await bootstrap(game)
    play = await game.client.get("/v1/modes", params={"domain": "film"})
    default = {m["key"]: m for m in play.json()}
    assert RANDOM_EMOJI in default and not {FRAME, EMOJI} & set(default)
    assert default[RANDOM_EMOJI]["round_options"] is None
    assert default[RANDOM_EMOJI]["min_items"] is None
    explicit = await game.client.get("/v1/modes", params={"domain": "film", "kind": "play"})
    assert explicit.json() == play.json()

    r = await game.client.get("/v1/modes", params={"domain": "film", "kind": "collection"})
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == play.headers["cache-control"]
    templates = {m["key"]: m for m in r.json()}
    assert set(templates) == {FRAME, EMOJI}  # только source collection и только домен
    frame = templates[FRAME]
    assert (frame["source_type"], frame["clue_type"]) == ("collection", "frame_progressive")
    assert frame["title_key"] == "modes.film.collection.frame.title"
    assert frame["round_options"] == [5, 10, 25, "all"]  # из конфига, без учёта сборника
    assert frame["min_items"] == 5
    assert [lvl["index"] for lvl in frame["levels"]] == list(range(1, 7))

    everything = (await game.client.get("/v1/modes", params={"kind": "collection"})).json()
    assert {FRAME, EMOJI, INTRO} <= {m["key"] for m in everything}
    assert all(m["source_type"] == "collection" for m in everything)
    assert (await game.client.get("/v1/modes", params={"kind": "other"})).status_code == 422


# ============================================================== играбельность черновика
async def test_playability_returns_only_playable_ids(game: Any) -> None:
    await bootstrap(game)
    ready = await films(game, 2)
    unready = await game.add_item(domain="film")
    music = await game.add_item(domain="music", ready_for=INTRO)
    token = game.issuer.issue(user_id=uuid7())
    ids = [ready[0], unready, music, uuid7(), ready[1], ready[0]]
    r = await game.client.post(
        f"/v1/modes/{FRAME}/playability",
        json={"media_item_ids": [str(i) for i in ids]},
        headers=bearer(token),
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"mode_key": FRAME, "playable_ids": [str(ready[0]), str(ready[1])]}
    assert r.headers["cache-control"] == "no-store"

    assert_problem(
        await game.client.post(
            f"/v1/modes/{RANDOM}/playability",
            json={"media_item_ids": [str(music)]},
            headers=bearer(token),
        ),
        422,
        "game.collection_not_supported",
    )
    assert_problem(
        await game.client.post(
            "/v1/modes/film.collection.none/playability",
            json={"media_item_ids": [str(music)]},
            headers=bearer(token),
        ),
        404,
        "game.mode_not_found",
    )


async def test_playability_is_rate_limited_per_user(game: Any) -> None:
    await bootstrap(game)
    token = game.issuer.issue(user_id=uuid7())
    body = {"media_item_ids": [str(uuid7())]}
    path = f"/v1/modes/{FRAME}/playability"
    for _ in range(30):  # GV_GAME_LIMITS__PLAYABILITY_PER_MIN по умолчанию
        assert (await game.client.post(path, json=body, headers=bearer(token))).status_code == 200
    assert_problem(
        await game.client.post(path, json=body, headers=bearer(token)), 429, "game.rate_limited"
    )


# ============================================================== хвост 3a: темы сетки
async def test_admin_theme_subjects_search(game: Any) -> None:
    await bootstrap(game)
    director = uuid7()
    for _ in range(32):
        await game.add_item(
            domain="film", persons=[(director, "Режиссёр Сетки", "director")], ready_for=BRACKET
        )
    path = f"/admin/v1/mode-configs/{BRACKET}/subjects"
    assert_problem(await game.client.get(path, headers=game.admin()), 403, "auth.forbidden")
    r = await game.client.get(path, params={"q": "сетки"}, headers=game.admin("game.daily.view"))
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"subject_id": str(director), "name": "Режиссёр Сетки", "role": "director", "items": 32}
    ]
    miss = await game.client.get(path, params={"q": "нолан"}, headers=game.admin("game.daily.view"))
    assert miss.json() == []
    assert_problem(
        await game.client.get(
            f"/admin/v1/mode-configs/{RANDOM}/subjects", headers=game.admin("game.daily.view")
        ),
        422,
        "game.subjects_not_supported",
    )
