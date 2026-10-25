"""Экраны прогрессии: срок блока главной, профиль игрока, порядок отказов (этап 5a)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

import pytest
from bff_web_service.application.dto import (
    AppearanceCard,
    AppearanceSlotCard,
    AvatarCard,
    BadgeRef,
    InventoryCard,
    PackCard,
    PlayerAppearanceCard,
    ProgressCard,
    PublicProgress,
    QuestsCard,
    ShopCatalogCard,
    SubjectKind,
    Viewer,
    WalletCard,
    WheelCard,
)
from bff_web_service.application.ports import UpstreamRejectedError, UpstreamUnavailableError
from bff_web_service.application.queries.progression import (
    GetPlayerPage,
    GetProfilePage,
    GetShellPage,
    GetShopPage,
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
        return await self._do("wallet", WalletCard(0, 0, 0))

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

    async def my_appearance(self, token: str) -> AppearanceCard:
        return await self._do(
            "my_appearance", AppearanceCard([AppearanceSlotCard("frame")], "/a/me.webp")
        )  # type: ignore[return-value]  # фейк

    async def inventory(self, token: str) -> InventoryCard:
        return await self._do("inventory", InventoryCard([]))  # type: ignore[return-value]  # фейк

    async def my_avatar(self, token: str) -> AvatarCard:
        return await self._do(
            "my_avatar", AvatarCard(entitled=True, request=None, max_bytes=1024, formats=["webp"])
        )  # type: ignore[return-value]  # фейк

    async def players_appearance(
        self, token: str, user_ids: list[UUID]
    ) -> list[PlayerAppearanceCard]:
        return await self._do("players_appearance", [])  # type: ignore[return-value]  # фейк

    async def shop_items(self, token: str) -> ShopCatalogCard:
        return await self._do("shop_items", ShopCatalogCard([], 0, []))  # type: ignore[return-value]  # фейк

    async def shop_packs(self, token: str) -> list[PackCard]:
        return await self._do("shop_packs", [])  # type: ignore[return-value]  # фейк

    async def my_events(self, token: str) -> list[object]:
        return await self._do("my_events", [])  # type: ignore[return-value]  # фейк

    async def active_event(self) -> object:
        return await self._do("active_event", None)

    async def event_page(self, token: str, event_key: str) -> object:
        return await self._do("event_page", None)


@dataclass
class FakeNotifications:
    count: int | Exception = 3
    calls: list[str] = field(default_factory=list)

    async def unread_count(self, token: str) -> int:
        self.calls.append(token)
        if isinstance(self.count, Exception):
            raise self.count
        return self.count


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


async def test_player_appearance_404_does_not_become_screen_404() -> None:
    """Игрок есть, а оформление недоступно - гаснет только блок оформления."""
    card = PublicProgress(user_id=PLAYER, level=1, total_xp=0, badges=[])
    fake = FakeProgression(public=card, fail={"players_appearance": rejected(404, "x.y")})
    page = await GetPlayerPage(fake, concurrency=5)(USER, PLAYER)  # type: ignore[arg-type]
    assert page.blocks.progress.is_ok
    assert page.blocks.appearance is not None and not page.blocks.appearance.is_ok


async def test_profile_appearance_and_avatar_hide_independently() -> None:
    """Адрес своей аватарки приходит с `/me/appearance`: сбой `/me/avatar` его не гасит."""
    fake = FakeProgression(fail={"my_avatar": UpstreamUnavailableError("progression", "t")})
    blocks = (await GetProfilePage(fake, concurrency=5)(USER)).blocks  # type: ignore[arg-type]
    assert blocks.progress.is_ok and blocks.inventory is not None and blocks.inventory.is_ok
    assert blocks.avatar is not None and not blocks.avatar.is_ok
    assert blocks.appearance is not None and blocks.appearance.is_ok
    assert blocks.appearance.data is not None
    assert blocks.appearance.data.custom_avatar_url == "/a/me.webp"

    down = FakeProgression(fail={"my_appearance": UpstreamUnavailableError("progression", "t")})
    blocks = (await GetProfilePage(down, concurrency=5)(USER)).blocks  # type: ignore[arg-type]
    assert blocks.appearance is not None and not blocks.appearance.is_ok
    assert blocks.avatar is not None and blocks.avatar.is_ok

    ok = (await GetProfilePage(FakeProgression(), concurrency=5)(USER)).blocks  # type: ignore[arg-type]
    assert ok.appearance is not None and ok.appearance.data is not None
    assert ok.appearance.data.custom_avatar_url == "/a/me.webp"


async def test_shop_requires_account_without_calls() -> None:
    fake = FakeProgression()
    page = GetShopPage(fake, concurrency=5)  # type: ignore[arg-type]
    with pytest.raises(UnauthenticatedError):
        await page(Viewer())
    with pytest.raises(ForbiddenError):
        await page(GUEST)
    assert fake.calls == []


async def test_shop_billing_disabled_means_null_packs_other_failures_unavailable() -> None:
    off = FakeProgression(fail={"shop_packs": rejected(404, "progression.billing_disabled")})
    blocks = (await GetShopPage(off, concurrency=5)(USER)).blocks  # type: ignore[arg-type]
    assert blocks.catalog.is_ok and blocks.packs is None

    other = FakeProgression(fail={"shop_packs": rejected(404, "progression.other")})
    blocks = (await GetShopPage(other, concurrency=5)(USER)).blocks  # type: ignore[arg-type]
    assert blocks.packs is not None and not blocks.packs.is_ok

    down = FakeProgression(fail={"shop_items": UpstreamUnavailableError("progression", "t")})
    blocks = (await GetShopPage(down, concurrency=5)(USER)).blocks  # type: ignore[arg-type]
    assert not blocks.catalog.is_ok and blocks.packs is not None and blocks.packs.is_ok


async def test_shop_source_403_is_screen_error() -> None:
    fake = FakeProgression(fail={"shop_items": rejected(403, "auth.forbidden")})
    with pytest.raises(ForbiddenError):
        await GetShopPage(fake, concurrency=5)(USER)  # type: ignore[arg-type]


async def test_shell_theme_is_key_only_and_bell_only_for_users() -> None:
    notifications = FakeNotifications()
    shell = GetShellPage(notifications, theme_key="autumn", concurrency=5)  # type: ignore[arg-type]

    for viewer in (Viewer(), GUEST, Viewer(token="x", token_rejected=True)):
        page = await shell(viewer)
        assert page.theme.key == "autumn" and page.bell is None
    assert notifications.calls == []

    page = await shell(USER)
    assert page.bell is not None and page.bell.data is not None and page.bell.data.unread == 3
    assert page.personal is True
    assert (await shell(Viewer())).personal is False
    assert (await shell(GUEST)).personal is True


async def test_shell_bell_failure_is_unavailable_not_error() -> None:
    for failure in (UpstreamUnavailableError("notifications", "t"), rejected(401, "a.b")):
        shell = GetShellPage(FakeNotifications(count=failure), theme_key="default", concurrency=5)  # type: ignore[arg-type]
        page = await shell(USER)
        assert page.bell is not None and not page.bell.is_ok
