"""Экраны прогрессии: срок блока главной, профиль игрока, порядок отказов (этап 5a)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

import pytest
from bff_web_service.application.dto import (
    BadgeRef,
    ProgressCard,
    PublicProgress,
    QuestsCard,
    SubjectKind,
    Viewer,
    WheelCard,
)
from bff_web_service.application.ports import UpstreamRejectedError
from bff_web_service.application.queries.progression import (
    GetPlayerPage,
    GetProfilePage,
    HomeProgress,
)

from gv_core.errors import ForbiddenError, NotFoundError, UnauthenticatedError

pytestmark = pytest.mark.unit

USER = Viewer(token="t", kind=SubjectKind.USER, verified=True)
GUEST = Viewer(token="g", kind=SubjectKind.GUEST, verified=True)
PLAYER = UUID("0192a3b4-c5d6-7e8f-9a0b-000000000901")
PROGRESS = ProgressCard(
    level=2,
    max_level=100,
    total_xp=600,
    xp_in_level=100,
    xp_to_next=400,
    solo_xp_today=0,
    solo_xp_cap=2000,
    streaks=[],
)


@dataclass
class FakeProgression:
    delay_s: float = 0
    fail: dict[str, Exception] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    public: PublicProgress | Exception | None = None

    async def _do(self, name: str, value: object) -> object:
        self.calls.append(name)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if name in self.fail:
            raise self.fail[name]
        return value

    async def progress(self, token: str) -> ProgressCard:
        return await self._do("progress", PROGRESS)  # type: ignore[return-value]  # фейк

    async def wallet(self, token: str) -> object:
        return await self._do("wallet", None)

    async def quests(self, token: str) -> QuestsCard:
        return await self._do("quests", QuestsCard([], []))  # type: ignore[return-value]  # фейк

    async def badges(self, token: str) -> list[object]:
        return await self._do("badges", [])  # type: ignore[return-value]  # фейк

    async def wheel(self, token: str) -> WheelCard:
        return await self._do(
            "wheel", WheelCard(enabled=True, can_spin=True, next_spin_at=None, sectors=[])
        )  # type: ignore[return-value]  # фейк

    async def player(self, token: str, user_id: UUID) -> PublicProgress:
        self.calls.append("player")
        if isinstance(self.public, Exception):
            raise self.public
        assert self.public is not None
        return self.public


def rejected(status: int, code: str) -> UpstreamRejectedError:
    return UpstreamRejectedError("progression", status, code)


async def test_home_progress_deadline_hides_block() -> None:
    fake = FakeProgression(delay_s=0.5)
    home = HomeProgress(fake, concurrency=5, deadline_s=0.05)  # type: ignore[arg-type]

    block = await home(USER)

    assert block is not None
    assert not block.is_ok


async def test_home_progress_never_calls_for_guest_or_rejected_token() -> None:
    fake = FakeProgression()
    home = HomeProgress(fake, concurrency=5, deadline_s=1)  # type: ignore[arg-type]

    assert await home(GUEST) is None
    assert await home(Viewer()) is None
    assert await home(Viewer(token="x", token_rejected=True)) is None
    assert fake.calls == []


async def test_profile_mixed_401_and_403_is_deterministic() -> None:
    """Первый по порядку блоков (progress, wallet, quests, badges, wheel) определяет ответ."""
    fake = FakeProgression(
        fail={
            "progress": rejected(403, "auth.forbidden"),
            "wheel": rejected(401, "auth.unauthenticated"),
        }
    )
    page = GetProfilePage(fake, concurrency=5)  # type: ignore[arg-type]
    with pytest.raises(ForbiddenError):
        await page(USER)

    fake = FakeProgression(
        fail={
            "wallet": rejected(401, "auth.unauthenticated"),
            "badges": rejected(403, "auth.forbidden"),
        }
    )
    page = GetProfilePage(fake, concurrency=5)  # type: ignore[arg-type]
    with pytest.raises(UnauthenticatedError):
        await page(USER)


async def test_player_page_requires_account_without_calls() -> None:
    fake = FakeProgression()
    page = GetPlayerPage(fake, concurrency=5)  # type: ignore[arg-type]

    with pytest.raises(UnauthenticatedError):
        await page(Viewer(), PLAYER)
    with pytest.raises(ForbiddenError):
        await page(GUEST, PLAYER)
    assert fake.calls == []


async def test_player_page_ok_and_source_failures() -> None:
    card = PublicProgress(
        user_id=PLAYER,
        level=5,
        total_xp=2600,
        badges=[BadgeRef("first_win", "Первая победа", datetime(2026, 10, 20, tzinfo=UTC))],
    )
    page = GetPlayerPage(FakeProgression(public=card), concurrency=5)  # type: ignore[arg-type]
    result = await page(USER, PLAYER)
    assert result.blocks.progress.data == card

    missing = GetPlayerPage(
        FakeProgression(public=rejected(404, "progression.user_not_found")),
        concurrency=5,  # type: ignore[arg-type]
    )
    with pytest.raises(NotFoundError):
        await missing(USER, PLAYER)

    down = GetPlayerPage(
        FakeProgression(public=rejected(418, "x")),
        concurrency=5,  # type: ignore[arg-type]
    )
    assert not (await down(USER, PLAYER)).blocks.progress.is_ok
