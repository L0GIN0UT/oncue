"""Тай-брейк мест лидерборда на живом Redis (ZREVRANGE): равные очки, детерминизм.

Контракт (``rank_standings``, ADR 0039): рейтинг по убыванию, при равенстве - ``user_id`` по
убыванию (строка). Порядок обязан быть одинаковым при любой записи (``put``/``replace``),
между вызовами и в ``rank``, иначе итоги сезона разойдутся с доской.
"""

from __future__ import annotations

import random
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from rating_service.domain.season import rank_standings
from rating_service.infrastructure.leaderboard import RedisLeaderboard
from redis.asyncio import Redis

from gv_core.ids import uuid7
from gv_testkit import InfraProvider

pytestmark = pytest.mark.integration

LADDER = "film"


@pytest.fixture
async def redis(infra: InfraProvider) -> AsyncIterator[Redis]:
    client = Redis.from_url(infra.redis_url())
    yield client
    await client.aclose()


def _players() -> list[tuple[UUID, int]]:
    rng = random.Random(7)
    ids = [uuid7() for _ in range(40)]
    ids.append(UUID("00000000-0000-7000-8000-000000000001"))  # крайние значения строки
    ids.append(UUID("ffffffff-ffff-7fff-8fff-ffffffffffff"))
    # Три группы с равными очками плюс одиночки: тай-брейк работает внутри каждой группы.
    return [(uid, rng.choice([1000, 1000, 1000, 1200, 1200, 900])) for uid in ids]


async def _read_all(board: RedisLeaderboard, season: UUID, *, step: int) -> list[tuple[UUID, int]]:
    rows: list[tuple[UUID, int]] = []
    while chunk := await board.page(LADDER, season, len(rows), step):
        rows.extend(chunk)
    return rows


@pytest.mark.parametrize("mode", ["put", "replace"])
async def test_equal_scores_break_ties_by_user_id_descending_and_are_stable(
    redis: Redis, mode: str
) -> None:
    season = uuid7()
    board = RedisLeaderboard(redis, ready_ttl_s=None)
    players = _players()
    shuffled = random.Random(1).sample(players, len(players))  # порядок записи не важен
    if mode == "put":
        for version, (uid, rating) in enumerate(shuffled, start=1):
            await board.put(LADDER, season, uid, rating, version)
    else:
        await board.replace(
            LADDER, season, [(uid, rating, 1) for uid, rating in shuffled], expires=False
        )

    expected = rank_standings(players, top_size=len(players))
    first = await _read_all(board, season, step=len(players))
    assert first == expected
    # Стабильность между вызовами и независимость от размера страницы.
    assert await _read_all(board, season, step=len(players)) == first
    assert await _read_all(board, season, step=5) == first
    # Место игрока совпадает с позицией на странице.
    for place, (uid, _) in enumerate(first):
        assert await board.rank(LADDER, season, uid) == place
