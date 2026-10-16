"""Сборка экранов из источников и деградация блоков (план этапа 2, §6)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from bff_web_service.application.dto import (
    ANONYMOUS,
    Bracket,
    BracketPick,
    DailyState,
    DailyStatus,
    Domain,
    Item,
    Me,
    Mode,
    ModeStart,
    Person,
    PersonalRecord,
    Presentation,
    ResultRecord,
    ResultRound,
    Reveal,
    SessionResult,
    Share,
    SubjectKind,
    SubjectPick,
    Viewer,
)
from bff_web_service.application.ports import UpstreamRejectedError, UpstreamUnavailableError
from bff_web_service.application.queries import (
    GetHomePage,
    GetResultsPage,
    GetSession,
    GetSoloPage,
    Limiter,
    PageLayout,
    daily_cards,
    mode_groups,
    period_cards,
    record_cards,
)
from bff_web_service.domain import Block, BlockStatus

from gv_core.errors import (
    AppError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthenticatedError,
)

pytestmark = pytest.mark.unit

DAY = date(2026, 10, 10)
RESET = datetime(2026, 10, 10, 21, 0, tzinfo=UTC)
SESSION = UUID("0192a3b4-c5d6-7e8f-9a0b-000000000700")
ITEM_A = UUID("0192a3b4-c5d6-7e8f-9a0b-0000000000a1")
ITEM_B = UUID("0192a3b4-c5d6-7e8f-9a0b-0000000000b2")
USER = Viewer(token="user-token", kind=SubjectKind.USER, verified=True)
GUEST = Viewer(token="guest-token", kind=SubjectKind.GUEST, verified=True)


def mode(
    key: str,
    domain: Domain,
    source_type: str,
    presentation: Presentation = "score",
    start: ModeStart | None = None,
) -> Mode:
    return Mode(
        key=key,
        version=1,
        domain=domain,
        title_key=f"modes.{key}",
        source_type=source_type,
        clue_type="audio_intro" if domain == "music" else "frame_progressive",
        rounds=5,
        allow_guest=True,
        answer_format="pick" if presentation != "score" else "classic",
        presentation=presentation,
        start=start,
    )


DIRECTOR = ModeStart(subject=SubjectPick(["director"], 3))
MODES = [
    mode("music.daily.classic", "music", "daily"),
    mode("film.daily.frame", "film", "daily"),
    mode("music.random.classic", "music", "random"),
    mode("film.random.frame", "film", "random"),
    mode("film.marathon.director", "film", "marathon", "streak", DIRECTOR),
    mode("film.pair.rating", "film", "pair", "streak"),
    mode("film.random.emoji", "film", "random"),
    mode("film.bracket.weekly", "film", "bracket", "bracket"),
]
LAYOUT = PageLayout()


def status(key: str, state: DailyState = "not_started") -> DailyStatus:
    return DailyStatus(
        mode_key=key,
        day=DAY,
        status=state,
        max_score=30,
        next_reset_at=RESET,
    )


def record(key: str, best: int = 7) -> PersonalRecord:
    return PersonalRecord(mode_key=key, best=best, achieved_at=RESET, percentile=81.5)


def result(*reveals: UUID | None) -> SessionResult:
    return SessionResult(
        score=11,
        max_score=30,
        rounds=[
            ResultRound(
                index=i,
                outcome="exact" if r else "void",
                level=1 if r else None,
                points=6 if r else 0,
                reveal=Reveal(r) if r else None,
            )
            for i, r in enumerate(reveals)
        ],
        share=Share("🟩⬛"),
    )


def item(item_id: UUID) -> Item:
    return Item(
        id=item_id,
        domain="film",
        kind="movie",
        title="Брат",
        original_title=None,
        year=1997,
        genres=["drama"],
        persons=[Person("Алексей Балабанов", "director")],
    )


@dataclass
class FakeGame:
    modes: list[Mode] | Exception = field(default_factory=lambda: list(MODES))
    dailies: list[DailyStatus] | Exception = field(
        default_factory=lambda: [status("music.daily.classic"), status("film.daily.frame")]
    )
    outcome: SessionResult | Exception = field(default_factory=lambda: result(ITEM_A))
    best: list[PersonalRecord] | Exception = field(
        default_factory=lambda: [record("film.pair.rating"), record("gone.pair.x")]
    )
    daily_calls: list[tuple[str, Domain | None]] = field(default_factory=list)
    records_calls: list[str] = field(default_factory=list)

    async def list_modes(self) -> list[Mode]:
        if isinstance(self.modes, Exception):
            raise self.modes
        return self.modes

    async def daily(self, token: str, domain: Domain | None) -> list[DailyStatus]:
        self.daily_calls.append((token, domain))
        if isinstance(self.dailies, Exception):
            raise self.dailies
        return self.dailies

    async def session_result(self, token: str, session_id: UUID) -> SessionResult:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    async def records(self, token: str) -> list[PersonalRecord]:
        self.records_calls.append(token)
        if isinstance(self.best, Exception):
            raise self.best
        return self.best


@dataclass
class FakeCatalog:
    items: dict[UUID, Item | Exception] = field(default_factory=dict)

    async def get_item(self, item_id: UUID) -> Item:
        value = self.items.get(item_id) or UpstreamRejectedError(
            "catalog", 404, "catalog.item_not_found"
        )
        if isinstance(value, Exception):
            raise value
        return value


@dataclass
class FakeIdentity:
    answer: Me | Exception = field(default_factory=lambda: Me(SESSION, "Нео"))
    calls: int = 0

    async def me(self, token: str) -> Me:
        self.calls += 1
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


DOWN = UpstreamUnavailableError("game", "timeout")


# ----------------------------------------------------------------------------- блок
def test_block_has_data_only_when_ok() -> None:
    assert Block.ok([1]).is_ok
    assert Block.unavailable().data is None
    with pytest.raises(ValueError, match="ok"):
        Block(BlockStatus.UNAVAILABLE, [1])
    with pytest.raises(ValueError, match="ok"):
        Block(BlockStatus.OK, None)


async def test_limiter_bounds_concurrency() -> None:
    limiter, active, peak = Limiter(2), 0, 0

    async def call() -> None:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1

    await asyncio.gather(*(limiter.run(call()) for _ in range(6)))
    assert peak == 2
    with pytest.raises(ValueError, match="limit"):
        Limiter(0)


# ----------------------------------------------------------------------------- главная
async def test_home_all_sources_ok() -> None:
    game = FakeGame()
    page = await GetHomePage(game, concurrency=10)(USER)
    assert page.personal is True
    assert page.blocks.modes.data is not None and len(page.blocks.modes.data) == len(MODES)
    dailies = page.blocks.dailies.data
    assert dailies is not None
    assert [(c.mode_key, c.domain, c.title_key) for c in dailies] == [
        ("music.daily.classic", "music", "modes.music.daily.classic"),
        ("film.daily.frame", "film", "modes.film.daily.frame"),
    ]
    assert game.daily_calls == [("user-token", None)]


async def test_home_weekly_bracket_is_separate_block_not_daily_card() -> None:
    game = FakeGame(dailies=[status("film.daily.frame"), status("film.bracket.weekly")])
    page = await GetHomePage(game, concurrency=10)(GUEST)
    assert page.blocks.dailies.data is not None
    assert [c.mode_key for c in page.blocks.dailies.data] == ["film.daily.frame"]
    assert page.blocks.weekly.data is not None
    assert [(c.mode_key, c.presentation) for c in page.blocks.weekly.data] == [
        ("film.bracket.weekly", "bracket")
    ]


async def test_home_game_down_degrades_blocks_not_page() -> None:
    page = await GetHomePage(FakeGame(modes=DOWN, dailies=DOWN), concurrency=10)(GUEST)
    assert page.blocks.modes.status is BlockStatus.UNAVAILABLE
    assert page.blocks.dailies.status is BlockStatus.UNAVAILABLE
    assert page.blocks.weekly.status is BlockStatus.UNAVAILABLE


async def test_home_daily_failure_keeps_modes() -> None:
    rejected = UpstreamRejectedError("game", 401, "auth.token_expired")
    page = await GetHomePage(FakeGame(dailies=rejected), concurrency=10)(USER)
    assert page.blocks.modes.is_ok
    assert page.blocks.dailies.status is BlockStatus.UNAVAILABLE


async def test_home_anonymous_is_public_and_skips_personal_call() -> None:
    game = FakeGame()
    page = await GetHomePage(game, concurrency=10)(ANONYMOUS)
    assert page.personal is False
    assert page.blocks.modes.is_ok
    assert page.blocks.dailies.status is BlockStatus.UNAVAILABLE
    assert game.daily_calls == []


def test_daily_cards_skip_statuses_of_unknown_modes() -> None:
    cards = daily_cards(MODES[:1], [status("music.daily.classic"), status("gone.daily.x")])
    assert [c.mode_key for c in cards] == ["music.daily.classic"]


# ----------------------------------------------------------------------------- хаб
async def test_solo_filters_by_domain_and_random_source_types() -> None:
    game = FakeGame(dailies=[status("film.daily.frame", "finished")])
    page = await GetSoloPage(game, concurrency=10, layout=LAYOUT)(USER, "film")
    assert page.blocks.random.data is not None
    assert [m.key for m in page.blocks.random.data] == ["film.random.frame", "film.random.emoji"]
    assert page.blocks.daily.data is not None
    assert [c.status for c in page.blocks.daily.data] == ["finished"]
    assert game.daily_calls == [("user-token", "film")]


async def test_solo_showcase_weekly_and_records_for_user() -> None:
    game = FakeGame(dailies=[status("film.daily.frame"), status("film.bracket.weekly")])
    page = await GetSoloPage(game, concurrency=10, layout=LAYOUT)(USER, "film")
    groups = page.blocks.modes.data
    assert groups is not None
    assert [(g.source_type, g.presentation, [m.key for m in g.modes]) for g in groups] == [
        ("random", "score", ["film.random.frame", "film.random.emoji"]),
        ("marathon", "streak", ["film.marathon.director"]),
        ("pair", "streak", ["film.pair.rating"]),
    ]
    assert groups[1].modes[0].start == DIRECTOR
    assert groups[2].modes[0].answer_format == "pick"
    assert page.blocks.weekly.data is not None
    assert [c.mode_key for c in page.blocks.weekly.data] == ["film.bracket.weekly"]
    assert page.blocks.daily.data is not None
    assert [c.mode_key for c in page.blocks.daily.data] == ["film.daily.frame"]
    assert page.blocks.records is not None and page.blocks.records.data is not None
    assert [(r.mode_key, r.best, r.presentation) for r in page.blocks.records.data] == [
        ("film.pair.rating", 7, "streak")
    ]
    assert game.records_calls == ["user-token"]


@pytest.mark.parametrize("viewer", [GUEST, ANONYMOUS])
async def test_solo_guest_and_anonymous_have_no_records(viewer: Viewer) -> None:
    game = FakeGame()
    page = await GetSoloPage(game, concurrency=10, layout=LAYOUT)(viewer, None)
    assert page.blocks.records is None
    assert page.blocks.modes.is_ok
    assert game.records_calls == []


async def test_solo_records_down_keeps_other_blocks() -> None:
    page = await GetSoloPage(FakeGame(best=DOWN), concurrency=10, layout=LAYOUT)(USER, "film")
    assert page.blocks.records is not None
    assert page.blocks.records.status is BlockStatus.UNAVAILABLE
    assert page.blocks.modes.is_ok and page.blocks.daily.is_ok and page.blocks.weekly.is_ok


async def test_solo_modes_down() -> None:
    page = await GetSoloPage(FakeGame(modes=DOWN), concurrency=10, layout=LAYOUT)(USER, None)
    blocks = page.blocks
    assert blocks.daily.status is BlockStatus.UNAVAILABLE
    assert blocks.random.status is BlockStatus.UNAVAILABLE
    assert blocks.modes.status is BlockStatus.UNAVAILABLE
    assert blocks.weekly.status is BlockStatus.UNAVAILABLE
    assert blocks.records is not None and blocks.records.status is BlockStatus.UNAVAILABLE


def test_mode_groups_follow_game_order_and_skip_scheduled() -> None:
    groups = mode_groups(MODES, exclude_source_types=LAYOUT.scheduled_source_types)
    assert [(g.source_type, g.presentation) for g in groups] == [
        ("random", "score"),
        ("marathon", "streak"),
        ("pair", "streak"),
    ]
    assert [m.key for m in groups[0].modes] == [
        "music.random.classic",
        "film.random.frame",
        "film.random.emoji",
    ]


def test_period_cards_unknown_scheduled_type_stays_daily() -> None:
    modes = [*MODES, mode("film.seasonal.x", "film", "seasonal")]
    daily, weekly = period_cards(
        modes,
        [status("film.seasonal.x"), status("film.bracket.weekly")],
        weekly_source_types=LAYOUT.weekly_source_types,
    )
    assert [c.mode_key for c in daily] == ["film.seasonal.x"]
    assert [c.mode_key for c in weekly] == ["film.bracket.weekly"]


def test_record_cards_skip_unknown_modes() -> None:
    cards = record_cards(MODES, [record("gone.pair.x"), record("film.pair.rating", 3)])
    assert [(c.mode_key, c.domain, c.title_key, c.percentile) for c in cards] == [
        ("film.pair.rating", "film", "modes.film.pair.rating", 81.5)
    ]


# ----------------------------------------------------------------------------- итог
async def test_results_pass_presentation_record_and_bracket() -> None:
    outcome = SessionResult(
        score=0,
        max_score=0,
        rounds=[],
        share=Share("🏆"),
        presentation="bracket",
        record=ResultRecord(best=12, is_new=True, percentile=90.0),
        bracket=Bracket("Брат", [BracketPick(5, "Брат", "Брат 2")]),
    )
    page = await GetResultsPage(FakeGame(outcome=outcome), FakeCatalog(), concurrency=10)(
        USER, SESSION
    )
    summary = page.blocks.result.data
    assert summary is not None
    assert summary.presentation == "bracket"
    assert summary.record == ResultRecord(best=12, is_new=True, percentile=90.0)
    assert summary.bracket == Bracket("Брат", [BracketPick(5, "Брат", "Брат 2")])
    assert page.blocks.items.data == []


async def test_results_joins_catalog_cards_and_skips_blocked_items() -> None:
    game = FakeGame(outcome=result(ITEM_A, None, ITEM_B, ITEM_A))
    catalog = FakeCatalog({ITEM_A: item(ITEM_A)})  # ITEM_B заблокирован → 404
    page = await GetResultsPage(game, catalog, concurrency=10)(USER, SESSION)
    summary = page.blocks.result.data
    assert summary is not None
    assert summary.session_id == SESSION
    assert [r.media_item_id for r in summary.rounds] == [ITEM_A, None, ITEM_B, ITEM_A]
    assert page.blocks.items.data is not None
    assert [c.media_item_id for c in page.blocks.items.data] == [ITEM_A]


async def test_results_catalog_down_keeps_result() -> None:
    catalog = FakeCatalog({ITEM_A: UpstreamUnavailableError("catalog", "http_503")})
    page = await GetResultsPage(FakeGame(), catalog, concurrency=10)(USER, SESSION)
    assert page.blocks.result.is_ok
    assert page.blocks.items.status is BlockStatus.UNAVAILABLE


async def test_results_game_down_degrades_both_blocks() -> None:
    page = await GetResultsPage(FakeGame(outcome=DOWN), FakeCatalog(), concurrency=10)(
        USER, SESSION
    )
    assert not page.blocks.result.is_ok
    assert not page.blocks.items.is_ok


async def test_results_require_token() -> None:
    with pytest.raises(UnauthenticatedError):
        await GetResultsPage(FakeGame(), FakeCatalog(), concurrency=10)(ANONYMOUS, SESSION)


@pytest.mark.parametrize(
    ("status_code", "code", "error", "expected_code"),
    [
        (401, "auth.token_expired", UnauthenticatedError, "auth.token_expired"),
        (403, "auth.forbidden", ForbiddenError, "auth.forbidden"),
        (404, "game.session_not_found", NotFoundError, "game.session_not_found"),
        (409, "game.session_not_finished", ConflictError, "game.session_not_finished"),
        # Прочие 4xx не раскрываются — для экрана это «нет такой сессии».
        (410, "game.whatever", NotFoundError, "resource.not_found"),
    ],
)
async def test_results_game_rejection_becomes_screen_error(
    status_code: int, code: str, error: type[AppError], expected_code: str
) -> None:
    game = FakeGame(outcome=UpstreamRejectedError("game", status_code, code))
    with pytest.raises(error) as caught:
        await GetResultsPage(game, FakeCatalog(), concurrency=10)(USER, SESSION)
    assert caught.value.code == expected_code


# ----------------------------------------------------------------------------- субъект
async def test_session_anonymous_and_guest_by_claim() -> None:
    identity = FakeIdentity()
    assert (await GetSession(identity)(ANONYMOUS)).subject is None
    guest = (await GetSession(identity)(GUEST)).subject
    assert guest is not None and guest.kind is SubjectKind.GUEST
    assert identity.calls == 0  # гостю identity отвечает 403 — не спрашиваем


async def test_session_user_with_name() -> None:
    subject = (await GetSession(FakeIdentity())(USER)).subject
    assert subject is not None
    assert (subject.kind, subject.display_name) == (SubjectKind.USER, "Нео")


@pytest.mark.parametrize(
    ("answer", "viewer", "expected"),
    [
        (UpstreamRejectedError("identity", 403, "auth.guest_forbidden"), Viewer("t"), "guest"),
        (UpstreamRejectedError("identity", 401, "auth.session_revoked"), USER, None),
        (UpstreamUnavailableError("identity", "timeout"), USER, "user"),
        (UpstreamUnavailableError("identity", "timeout"), Viewer("t"), None),
    ],
)
async def test_session_identity_failures(
    answer: Exception, viewer: Viewer, expected: str | None
) -> None:
    subject = (await GetSession(FakeIdentity(answer))(viewer)).subject
    assert (subject.kind.value if subject else None) == expected
    if subject is not None:
        assert subject.display_name is None
