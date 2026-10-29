"""Друзья и дейлик: лента главной, «среди друзей» итога, награды за игру (PR-A, пункты 1 и 4)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from bff_web_service.application.dto import (
    ANONYMOUS,
    AppearanceCard,
    DailyResult,
    DailyStatus,
    Domain,
    FriendCard,
    Mode,
    PlayerAppearanceCard,
    PlayerRelation,
    RewardStreakCard,
    SessionResult,
    SessionRewardsCard,
    Share,
    SubjectKind,
    Viewer,
)
from bff_web_service.application.ports import UpstreamRejectedError, UpstreamUnavailableError
from bff_web_service.application.queries import GetHomePage, HomeFeed, ResultRewards, ResultsFriends
from bff_web_service.application.queries.feed import FeedLimits, FriendsDaily
from bff_web_service.domain import BlockStatus

pytestmark = pytest.mark.unit

DAY = date(2026, 10, 10)
MUSIC, FILM = "music.daily.classic", "film.daily.frame"
SESSION = UUID("0192a3b4-c5d6-7e8f-9a0b-000000000700")
USER = Viewer(token="user-token", kind=SubjectKind.USER, verified=True)
GUEST = Viewer(token="guest-token", kind=SubjectKind.GUEST, verified=True)


def uid(n: int) -> UUID:
    return UUID(f"0192a3b4-c5d6-7e8f-9a0b-0000000009{n:02d}")


def friend(n: int, nickname: str | None = None) -> FriendCard:
    return FriendCard(uid(n), nickname or f"friend{n}", "offline")


def played(n: int, mode_key: str, score: int, day: date = DAY) -> DailyResult:
    domain: Domain = "music" if mode_key == MUSIC else "film"
    return DailyResult(uid(n), mode_key, domain, day, score, 30)


def relation(*, stats: bool = True, blocked: bool = False) -> PlayerRelation:
    return PlayerRelation(
        profile_visible=True,
        stats_visible=stats,
        is_friend=True,
        can_send_request=False,
        blocked_by_me=blocked,
    )


@dataclass
class FakeSocial:
    friends_list: list[FriendCard] | Exception = field(default_factory=list)
    hidden: set[UUID] = field(default_factory=set)
    blocked: set[UUID] = field(default_factory=set)
    relations_error: Exception | None = None
    relation_calls: list[list[UUID]] = field(default_factory=list)
    friends_calls: int = 0

    async def friends(self, token: str) -> list[FriendCard]:
        self.friends_calls += 1
        if isinstance(self.friends_list, Exception):
            raise self.friends_list
        return self.friends_list

    async def relations(self, token: str, user_ids: list[UUID]) -> dict[UUID, PlayerRelation]:
        self.relation_calls.append(list(user_ids))
        if self.relations_error is not None:
            raise self.relations_error
        return {
            u: relation(stats=u not in self.hidden, blocked=u in self.blocked) for u in user_ids
        }


@dataclass
class FakeGame:
    results: list[DailyResult] | Exception = field(default_factory=list)
    statuses: list[DailyStatus] | Exception = field(default_factory=list)
    result_calls: list[list[UUID]] = field(default_factory=list)
    status_calls: int = 0
    delay_s: float = 0

    async def daily_results(
        self, token: str, user_ids: list[UUID], domain: Domain | None = None
    ) -> list[DailyResult]:
        self.result_calls.append(list(user_ids))
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if isinstance(self.results, Exception):
            raise self.results
        return [r for r in self.results if r.user_id in user_ids]

    async def list_modes(self) -> list[Mode]:
        return []

    async def daily(self, token: str, domain: Domain | None) -> list[DailyStatus]:
        self.status_calls += 1
        if isinstance(self.statuses, Exception):
            raise self.statuses
        return self.statuses


@dataclass
class FakeProgression:
    appearance_error: Exception | None = None
    appearance_calls: list[list[UUID]] = field(default_factory=list)
    rewards: list[SessionRewardsCard | Exception] = field(default_factory=list)
    reward_calls: int = 0

    async def players_appearance(
        self, token: str, user_ids: list[UUID]
    ) -> list[PlayerAppearanceCard]:
        self.appearance_calls.append(list(user_ids))
        if self.appearance_error is not None:
            raise self.appearance_error
        return [PlayerAppearanceCard(u, AppearanceCard(slots=[])) for u in user_ids]

    async def session_rewards(self, token: str, session_id: UUID) -> SessionRewardsCard:
        self.reward_calls += 1
        value = self.rewards[min(self.reward_calls, len(self.rewards)) - 1]
        if isinstance(value, Exception):
            raise value
        return value


def limits(**over: float) -> FeedLimits:
    base = {
        "scan_limit": 100,
        "results_batch": 50,
        "relations_batch": 50,
        "appearance_batch": 12,
        "deadline_s": 1.0,
    }
    return FeedLimits(**{**base, **over})  # type: ignore[arg-type]


def pipeline(
    social: FakeSocial, game: FakeGame, progression: FakeProgression, **over: float
) -> FriendsDaily:
    return FriendsDaily(social, game, progression, concurrency=10, limits=limits(**over))  # type: ignore[arg-type]


def feed(
    social: FakeSocial,
    game: FakeGame,
    progression: FakeProgression | None = None,
    *,
    items_limit: int = 10,
    **over: float,
) -> HomeFeed:
    return HomeFeed(
        pipeline(social, game, progression or FakeProgression(), **over),
        concurrency=10,
        items_limit=items_limit,
    )


DOWN = UpstreamUnavailableError("social", "timeout")
REJECTED = UpstreamRejectedError("social", 403, "auth.forbidden")


# ----------------------------------------------------------------------------- лента главной
@pytest.mark.parametrize("viewer", [ANONYMOUS, GUEST])
async def test_feed_is_null_without_account_and_calls_nothing(viewer: Viewer) -> None:
    social, game, progression = FakeSocial([friend(1)]), FakeGame(), FakeProgression()

    assert await feed(social, game, progression)(viewer) is None

    assert social.friends_calls == 0
    assert game.result_calls == []
    assert progression.appearance_calls == []


async def test_feed_counts_played_of_all_friends_and_ranks_results() -> None:
    social = FakeSocial([friend(n) for n in range(1, 10)])  # 9 друзей
    game = FakeGame(
        [
            played(1, MUSIC, 12),
            played(1, FILM, 10),  # оба домена - первым
            played(2, MUSIC, 25),
            played(3, FILM, 30),
            played(4, MUSIC, 5),
        ]
    )
    progression = FakeProgression()

    block = await feed(social, game, progression)(USER)

    assert block is not None and block.data is not None
    card = block.data
    assert (card.friends_total, card.friends_played) == (9, 4)
    assert [f.user_id for f in card.items] == [uid(1), uid(3), uid(2), uid(4)]
    both = card.items[0]
    assert [(r.domain, r.mode_key, r.score) for r in both.results] == [
        ("film", FILM, 10),
        ("music", MUSIC, 12),
    ]
    assert all(f.appearance is not None for f in card.items)
    # Результаты - у всех друзей одной пачкой, видимость и оформление - только у сыгравших.
    assert game.result_calls == [[uid(n) for n in range(1, 10)]]
    assert social.relation_calls == [[uid(1), uid(2), uid(3), uid(4)]]


async def test_feed_hides_friends_with_closed_stats_everywhere() -> None:
    social = FakeSocial([friend(1), friend(2), friend(3)], hidden={uid(2)}, blocked={uid(3)})
    game = FakeGame([played(1, MUSIC, 10), played(2, MUSIC, 29), played(3, MUSIC, 28)])
    progression = FakeProgression()

    block = await feed(social, game, progression)(USER)

    assert block is not None and block.data is not None
    assert block.data.friends_total == 3
    assert block.data.friends_played == 1  # скрытый друг не раскрывается и в счётчике
    assert [f.user_id for f in block.data.items] == [uid(1)]
    assert progression.appearance_calls == [[uid(1)]]


async def test_feed_without_friends_makes_no_further_calls() -> None:
    social, game, progression = FakeSocial([]), FakeGame(), FakeProgression()

    block = await feed(social, game, progression)(USER)

    assert block is not None and block.data is not None
    assert (block.data.friends_total, block.data.friends_played, block.data.items) == (0, 0, [])
    assert game.result_calls == [] and social.relation_calls == []
    assert progression.appearance_calls == []


async def test_feed_batches_and_scan_limit_come_from_settings() -> None:
    friends = [friend(n) for n in range(1, 8)]
    results = [played(n, MUSIC, n) for n in range(1, 8)]
    social, game, progression = FakeSocial(friends), FakeGame(results), FakeProgression()

    block = await feed(
        social,
        game,
        progression,
        items_limit=3,
        scan_limit=5,
        results_batch=2,
        relations_batch=2,
        appearance_batch=2,
    )(USER)

    assert block is not None and block.data is not None
    # опрошены первые 5 из 7 друзей пачками по 2; всего друзей - все 7
    assert game.result_calls == [[uid(1), uid(2)], [uid(3), uid(4)], [uid(5)]]
    assert all(len(c) <= 2 for c in social.relation_calls) and len(social.relation_calls) == 3
    assert block.data.friends_total == 7
    assert block.data.friends_played == 5
    assert len(block.data.items) == 3  # items_limit режет показ, не счётчик
    assert [len(c) for c in progression.appearance_calls] == [2, 1]


@pytest.mark.parametrize(
    "part", ["friends", "game", "relations", "appearance"], ids=lambda p: f"{p}-down"
)
@pytest.mark.parametrize("error", [DOWN, REJECTED], ids=["unavailable", "rejected"])
async def test_feed_any_part_failing_gives_unavailable(part: str, error: Exception) -> None:
    social = FakeSocial(error if part == "friends" else [friend(1)])
    game = FakeGame(error if part == "game" else [played(1, MUSIC, 9)])
    if part == "relations":
        social.relations_error = error
    progression = FakeProgression(appearance_error=error if part == "appearance" else None)

    block = await feed(social, game, progression)(USER)

    assert block is not None
    assert block.status is BlockStatus.UNAVAILABLE and block.data is None


async def test_feed_deadline_turns_block_unavailable() -> None:
    social = FakeSocial([friend(1)])
    game = FakeGame([played(1, MUSIC, 9)], delay_s=0.5)

    block = await feed(social, game, deadline_s=0.05)(USER)

    assert block is not None and block.status is BlockStatus.UNAVAILABLE


async def test_home_page_survives_feed_failure() -> None:
    social, game = FakeSocial(DOWN), FakeGame()

    page = await GetHomePage(game, concurrency=10, feed=feed(social, game))(USER)  # type: ignore[arg-type]

    assert page.blocks.feed is not None and page.blocks.feed.status is BlockStatus.UNAVAILABLE
    assert page.blocks.dailies.status is BlockStatus.OK
    assert page.blocks.modes.status is BlockStatus.OK


async def test_home_page_for_anonymous_has_no_feed_and_asks_no_friends() -> None:
    social, game = FakeSocial([friend(1)]), FakeGame()

    page = await GetHomePage(game, concurrency=10, feed=feed(social, game))(ANONYMOUS)  # type: ignore[arg-type]

    assert page.blocks.feed is None
    assert social.friends_calls == 0


# ----------------------------------------------------------------------------- среди друзей
def daily_result(score: int = 20, *, daily: bool = True, day: date | None = DAY) -> SessionResult:
    return SessionResult(
        score=score,
        max_score=30,
        rounds=[],
        share=Share("OnCue"),
        mode_key=MUSIC,
        domain="music",
        day=day,
        daily=daily,
    )


def status(day: date = DAY, mode_key: str = MUSIC) -> DailyStatus:
    return DailyStatus(
        mode_key=mode_key,
        day=day,
        status="finished",
        max_score=30,
        next_reset_at=datetime(2026, 10, 10, 21, 0, tzinfo=UTC),
    )


def friends_today(
    social: FakeSocial, game: FakeGame, progression: FakeProgression | None = None, **over: float
) -> ResultsFriends:
    return ResultsFriends(
        pipeline(social, game, progression or FakeProgression(), **over),
        game,  # type: ignore[arg-type]
        concurrency=10,
        items_limit=2,
    )


@pytest.mark.parametrize("viewer", [ANONYMOUS, GUEST])
async def test_friends_today_is_null_without_account(viewer: Viewer) -> None:
    social, game = FakeSocial([friend(1)]), FakeGame()
    assert await friends_today(social, game)(viewer, daily_result()) is None
    assert social.friends_calls == 0 and game.status_calls == 0


@pytest.mark.parametrize(
    "result",
    [
        daily_result(daily=False),
        SessionResult(score=1, max_score=2, rounds=[], share=Share("x"), daily=True),
    ],
    ids=["not-daily", "no-mode-data"],
)
async def test_friends_today_is_null_for_non_daily_without_calls(result: SessionResult) -> None:
    social, game = FakeSocial([friend(1)]), FakeGame()
    assert await friends_today(social, game)(USER, result) is None
    assert social.friends_calls == 0 and game.result_calls == []


async def test_friends_today_compares_with_average_and_place() -> None:
    social = FakeSocial([friend(n) for n in range(1, 6)], hidden={uid(5)})
    game = FakeGame(
        [
            played(1, MUSIC, 30),
            played(2, MUSIC, 20),  # столько же, сколько у зрителя: место делится
            played(3, MUSIC, 10),
            played(4, FILM, 29),  # другой режим в сравнение не идёт
            played(5, MUSIC, 29),  # статистика закрыта
        ],
        statuses=[status()],
    )

    block = await friends_today(social, game)(USER, daily_result(20))

    assert block is not None and block.data is not None
    card = block.data
    assert (card.mode_key, card.domain, card.day) == (MUSIC, "music", DAY)
    assert (card.my_score, card.my_max_score, card.my_place) == (20, 30, 2)
    assert (card.friends_total, card.friends_played) == (5, 3)
    assert card.average_score == 20.0
    assert [(r.user_id, r.score) for r in card.items] == [(uid(1), 30), (uid(2), 20)]  # лимит 2


async def test_friends_today_with_nobody_played_still_shows_first_place() -> None:
    social, game = FakeSocial([friend(1)]), FakeGame([], statuses=[status()])

    block = await friends_today(social, game)(USER, daily_result(7))

    assert block is not None and block.data is not None
    assert (block.data.my_place, block.data.friends_played) == (1, 0)
    assert block.data.average_score is None and block.data.items == []


async def test_friends_today_is_null_when_the_period_is_over() -> None:
    social = FakeSocial([friend(1)])
    game = FakeGame([played(1, MUSIC, 9)], statuses=[status(day=date(2026, 10, 11))])

    assert await friends_today(social, game)(USER, daily_result(20)) is None


@pytest.mark.parametrize("part", ["friends", "game", "status"])
async def test_friends_today_failing_part_gives_unavailable(part: str) -> None:
    social = FakeSocial(DOWN if part == "friends" else [friend(1)])
    game = FakeGame(
        DOWN if part == "game" else [played(1, MUSIC, 9)],
        statuses=DOWN if part == "status" else [status()],
    )

    block = await friends_today(social, game)(USER, daily_result())

    assert block is not None and block.status is BlockStatus.UNAVAILABLE


# ----------------------------------------------------------------------------- награды
def rewards_value(*, settled: bool = True, before: int = 1, after: int = 2) -> SessionRewardsCard:
    return SessionRewardsCard(
        session_id=SESSION,
        settled=settled,
        xp=60 if settled else 0,
        verses=5 if settled else 0,
        level_before=before if settled else None,
        level_after=after if settled else None,
        streak=RewardStreakCard("music", 3) if settled else None,
    )


def rewards(progression: FakeProgression, *, retries: int = 2) -> ResultRewards:
    return ResultRewards(
        progression,  # type: ignore[arg-type]
        concurrency=10,
        deadline_s=1.0,
        retries=retries,
        retry_delay_s=0,
    )


@pytest.mark.parametrize("viewer", [ANONYMOUS, GUEST])
async def test_rewards_are_null_without_account(viewer: Viewer) -> None:
    progression = FakeProgression(rewards=[rewards_value()])
    assert await rewards(progression)(viewer, SESSION) is None
    assert progression.reward_calls == 0


async def test_rewards_settled_reports_level_up_and_streak() -> None:
    progression = FakeProgression(rewards=[rewards_value()])

    block = await rewards(progression)(USER, SESSION)

    assert block is not None and block.data is not None
    card = block.data
    assert (card.settled, card.xp, card.verses) == (True, 60, 5)
    assert (card.level_before, card.level_after, card.leveled_up) == (1, 2, True)
    assert card.streak == RewardStreakCard("music", 3)
    assert progression.reward_calls == 1


async def test_rewards_no_level_up_when_level_is_the_same() -> None:
    progression = FakeProgression(rewards=[rewards_value(before=3, after=3)])
    block = await rewards(progression)(USER, SESSION)
    assert block is not None and block.data is not None and block.data.leveled_up is False


async def test_rewards_pending_is_retried_then_settles() -> None:
    progression = FakeProgression(rewards=[rewards_value(settled=False), rewards_value()])

    block = await rewards(progression)(USER, SESSION)

    assert block is not None and block.data is not None and block.data.settled is True
    assert progression.reward_calls == 2


async def test_rewards_still_pending_is_ok_block_not_an_error() -> None:
    progression = FakeProgression(rewards=[rewards_value(settled=False)])

    block = await rewards(progression, retries=2)(USER, SESSION)

    assert block is not None and block.status is BlockStatus.OK and block.data is not None
    assert (block.data.settled, block.data.xp, block.data.verses) == (False, 0, 0)
    assert progression.reward_calls == 3  # первая попытка и два повтора


@pytest.mark.parametrize("error", [DOWN, REJECTED], ids=["unavailable", "rejected"])
async def test_rewards_source_failure_gives_unavailable_without_retry(error: Exception) -> None:
    progression = FakeProgression(rewards=[error])

    block = await rewards(progression)(USER, SESSION)

    assert block is not None and block.status is BlockStatus.UNAVAILABLE
    assert progression.reward_calls == 1
