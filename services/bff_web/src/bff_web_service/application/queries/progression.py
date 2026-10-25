"""Экраны прогрессии (план этапа 5, §7; D1–D8): профиль и свёртка прогресса для главной.

Профиль — только у пользователя: аноним получает 401, гость — 403 ``auth.guest_forbidden``,
progression при этом не вызывается. Блоки гаснут независимо; отказ токена (401/403) источником —
ответ экрана. Тексты заданий, бейджей и секторов приходят из progression, здесь их нет.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import structlog

from bff_web_service.application.dto import (
    AppearanceCard,
    BellCard,
    EventArchiveCard,
    HomeProgressCard,
    PlayerBlocks,
    PlayerPage,
    PlayerRelation,
    PlayerSocialCard,
    ProfileBlocks,
    ProfilePage,
    ProgressCard,
    PublicProgress,
    QuestsCard,
    SanctionsCard,
    ShellPage,
    ShopBlocks,
    ShopPage,
    SubjectKind,
    ThemeCard,
    Viewer,
    WheelCard,
)
from bff_web_service.application.ports import (
    NotificationsClient,
    ProgressionClient,
    SocialClient,
    UpstreamRejectedError,
    UpstreamUnavailableError,
)
from bff_web_service.application.queries.common import (
    GUEST_FORBIDDEN,
    HTTP_FORBIDDEN,
    HTTP_NOT_FOUND,
    HTTP_UNAUTHORIZED,
    Limiter,
    UpstreamError,
    block_of,
    maybe,
    screen_error,
)
from bff_web_service.application.queries.events import EventsContext
from bff_web_service.application.queries.rating import user_token
from bff_web_service.domain import Block
from gv_core.errors import ForbiddenError, UnauthenticatedError

log = structlog.get_logger(__name__)

BILLING_DISABLED = "progression.billing_disabled"
"""Код 404 паков: продажа выключена (``billing.enabled=false``) - блок ``packs`` равен ``null``."""


class GetProfilePage:
    """Профиль: уровень, Версы, задания, бейджи, колесо."""

    def __init__(self, progression: ProgressionClient, *, concurrency: int) -> None:
        self._progression, self._concurrency = progression, concurrency

    async def __call__(self, viewer: Viewer) -> ProfilePage:
        """Собрать страницу.

        Raises:
            UnauthenticatedError: аноним или progression не принял токен.
            ForbiddenError: ``auth.guest_forbidden`` — гость (или 403 источника).
        """
        if viewer.token is None or viewer.token_rejected:
            raise UnauthenticatedError("требуется вход")
        if viewer.kind is SubjectKind.GUEST:
            raise ForbiddenError("профиль доступен только пользователям", code=GUEST_FORBIDDEN)
        token, p = viewer.token, self._progression
        limiter = Limiter(self._concurrency)
        # Две группы под одним gather: так вызовы идут параллельно, а типы результатов сохранены.
        core, cosmetics = await asyncio.gather(
            asyncio.gather(
                limiter.attempt(p.progress(token)),
                limiter.attempt(p.wallet(token)),
                limiter.attempt(p.quests(token)),
                limiter.attempt(p.badges(token)),
                limiter.attempt(p.wheel(token)),
            ),
            asyncio.gather(
                limiter.attempt(p.my_appearance(token)),
                limiter.attempt(p.inventory(token)),
                limiter.attempt(p.my_avatar(token)),
                limiter.attempt(p.my_events(token)),
            ),
        )
        progress, wallet, quests, badges, wheel = core
        slots, inventory, avatar, events = cosmetics
        _raise_auth_failure(progress, wallet, quests, badges, wheel, slots, inventory, avatar)
        return ProfilePage(
            blocks=ProfileBlocks(
                progress=block_of(progress, "profile.progress"),
                wallet=block_of(wallet, "profile.wallet"),
                quests=block_of(quests, "profile.quests"),
                badges=block_of(badges, "profile.badges"),
                wheel=block_of(wheel, "profile.wheel"),
                appearance=block_of(slots, "profile.appearance"),
                inventory=block_of(inventory, "profile.inventory"),
                avatar=block_of(avatar, "profile.avatar"),
                # Архив - дополнение: сбой источника даёт пустой список, не падение профиля.
                events=_archive(events),
            )
        )


def _archive(events: list[EventArchiveCard] | UpstreamError) -> list[EventArchiveCard]:
    """Архив событий; источник недоступен - пустой список, причина - в лог."""
    if isinstance(events, list):
        return events
    block_of(events, "profile.events")
    return []


class GetPlayerPage:
    """Профиль чужого игрока: публичный прогресс (уровень, опыт, бейджи). Только с аккаунтом."""

    def __init__(
        self,
        progression: ProgressionClient,
        *,
        concurrency: int,
        social: SocialClient | None = None,
    ) -> None:
        self._progression, self._concurrency = progression, concurrency
        self._social = social

    async def __call__(self, viewer: Viewer, user_id: UUID) -> PlayerPage:
        """Собрать страницу.

        Приватность (5c): сначала отношение из social. Скрытый профиль, блокировка и
        неизвестный игрок неразличимы — ``restricted``, статистика не запрашивается. Скрытая
        статистика при видимом профиле — ``progress`` ``unavailable`` без вызова progression.
        Social недоступен — статистика чужого игрока не отдаётся (приватность важнее
        доступности), оформление остаётся.

        Raises:
            UnauthenticatedError: аноним или progression не принял токен.
            ForbiddenError: ``auth.guest_forbidden`` — гость.
            NotFoundError: игрока нет (404 progression).
        """
        if viewer.token is None or viewer.token_rejected:
            raise UnauthenticatedError("требуется вход")
        if viewer.kind is SubjectKind.GUEST:
            raise ForbiddenError(
                "профиль игрока доступен только пользователям", code=GUEST_FORBIDDEN
            )
        limiter = Limiter(self._concurrency)
        # Свой профиль и социальные ограничения не касаются: social не вызывается.
        is_self = viewer.user_id is not None and viewer.user_id == user_id
        relation: PlayerRelation | UpstreamError | None = None
        if self._social is not None and not is_self:
            relation = await limiter.attempt(self._social.relation(viewer.token, user_id))
        social_block: Block[PlayerSocialCard] | None = None
        if relation is not None:
            social_block = (
                Block.ok(
                    PlayerSocialCard(
                        relation.is_friend, relation.can_send_request, relation.blocked_by_me
                    )
                )
                if isinstance(relation, PlayerRelation)
                else block_of(relation, "player.social")
            )
        stats_allowed = relation is None or (
            isinstance(relation, PlayerRelation) and relation.stats_visible
        )
        restricted = isinstance(relation, PlayerRelation) and not relation.profile_visible
        stats_call = (
            limiter.attempt(self._progression.player(viewer.token, user_id))
            if stats_allowed and not restricted
            else None
        )
        result, looks = await asyncio.gather(
            maybe(stats_call),
            limiter.attempt(self._progression.players_appearance(viewer.token, [user_id])),
        )
        if isinstance(result, UpstreamRejectedError) and (
            error := screen_error(result, not_found="игрок не найден")
        ):
            raise error
        _raise_auth_failure(looks)  # 404 оформления - не 404 экрана: игрок есть
        appearance: Block[AppearanceCard]
        if isinstance(looks, list):
            # Источник пропускает неизвестных: игрока без оформления показываем «по умолчанию».
            mine = next((p.appearance for p in looks if p.user_id == user_id), None)
            appearance = Block.ok(mine or AppearanceCard(slots=[]))
        else:
            appearance = block_of(looks, "player.appearance")
        progress: Block[PublicProgress] = (
            Block.unavailable() if result is None else block_of(result, "player.progress")
        )
        nickname = (
            relation.nickname
            if isinstance(relation, PlayerRelation) and relation.profile_visible
            else None
        )
        return PlayerPage(
            blocks=PlayerBlocks(progress=progress, appearance=appearance, social=social_block),
            restricted=restricted,
            nickname=nickname,
        )


class GetShopPage:
    """Магазин: витрина косметики с балансом и паки (продажа паков может быть выключена)."""

    def __init__(self, progression: ProgressionClient, *, concurrency: int) -> None:
        self._progression, self._concurrency = progression, concurrency

    async def __call__(self, viewer: Viewer) -> ShopPage:
        """Собрать страницу.

        Raises:
            UnauthenticatedError: аноним или progression не принял токен.
            ForbiddenError: ``auth.guest_forbidden`` — гость (источники не вызываются).
        """
        if viewer.token is None or viewer.token_rejected:
            raise UnauthenticatedError("требуется вход")
        if viewer.kind is SubjectKind.GUEST:
            raise ForbiddenError("магазин доступен только пользователям", code=GUEST_FORBIDDEN)
        token, limiter = viewer.token, Limiter(self._concurrency)
        catalog, packs = await asyncio.gather(
            limiter.attempt(self._progression.shop_items(token)),
            limiter.attempt(self._progression.shop_packs(token)),
        )
        billing_off = (
            isinstance(packs, UpstreamRejectedError)
            and packs.status == HTTP_NOT_FOUND
            and packs.code == BILLING_DISABLED
        )
        _raise_auth_failure(catalog, packs)
        return ShopPage(
            blocks=ShopBlocks(
                catalog=block_of(catalog, "shop.catalog"),
                packs=None if billing_off else block_of(packs, "shop.packs"),
            )
        )


class GetShellPage:
    """Оболочка: ключ темы (настройка) и колокольчик (только пользователю)."""

    def __init__(
        self,
        notifications: NotificationsClient,
        *,
        theme_key: str,
        concurrency: int,
        social: SocialClient | None = None,
        events: EventsContext | None = None,
    ) -> None:
        self._notifications, self._theme_key = notifications, theme_key
        self._concurrency = concurrency
        self._social = social
        self._events = events

    async def __call__(self, viewer: Viewer) -> ShellPage:
        """Собрать оболочку; сбой notifications гасит колокольчик, social — санкции.

        Тема и баннер - от активного события (5d); progression недоступен - тема по умолчанию.
        """
        snapshot = await self._events.snapshot(viewer) if self._events is not None else None
        theme = ThemeCard(key=snapshot.theme_key if snapshot is not None else self._theme_key)
        token = user_token(viewer)
        bell: Block[BellCard] | None = None
        sanctions: Block[SanctionsCard] | None = None
        if token is not None:
            limiter = Limiter(self._concurrency)
            count, banned = await asyncio.gather(
                limiter.attempt(self._notifications.unread_count(token)),
                maybe(
                    limiter.attempt(self._social.sanctions(token))
                    if self._social is not None
                    else None
                ),
            )
            if isinstance(count, UpstreamUnavailableError | UpstreamRejectedError):
                bell = block_of(count, "shell.bell")
            else:
                bell = Block.ok(BellCard(unread=count))
            if banned is not None:
                sanctions = block_of(banned, "shell.sanctions")
        return ShellPage(
            theme=theme,
            bell=bell,
            personal=viewer.token is not None,
            sanctions=sanctions,
            event=snapshot.banner if snapshot is not None else None,
        )


def _raise_auth_failure(*results: object) -> None:
    """Отказ токена (401/403) любым источником - ответ экрана (первый по порядку)."""
    for result in results:
        if isinstance(result, UpstreamRejectedError) and result.status in (
            HTTP_UNAUTHORIZED,
            HTTP_FORBIDDEN,
        ):
            error = screen_error(result, not_found="")
            if error is not None:
                raise error


class HomeProgress:
    """Блок ``progress`` главной: свёртка трёх источников progression под общим сроком."""

    def __init__(
        self, progression: ProgressionClient, *, concurrency: int, deadline_s: float
    ) -> None:
        self._progression = progression
        self._concurrency, self._deadline_s = concurrency, deadline_s

    async def __call__(self, viewer: Viewer) -> Block[HomeProgressCard] | None:
        """``None`` — гость или аноним (progression не вызывается); иначе блок."""
        token = user_token(viewer)
        if token is None:
            return None
        limiter = Limiter(self._concurrency)
        p = self._progression
        try:
            async with asyncio.timeout(self._deadline_s):
                progress, quests, wheel = await asyncio.gather(
                    limiter.attempt(p.progress(token)),
                    limiter.attempt(p.quests(token)),
                    limiter.attempt(p.wheel(token)),
                )
        except TimeoutError:
            log.warning("bff.block_unavailable", block="home.progress", reason="deadline")
            return Block.unavailable()
        if not (
            isinstance(progress, ProgressCard)
            and isinstance(quests, QuestsCard)
            and isinstance(wheel, WheelCard)
        ):
            for result in (progress, quests, wheel):
                block_of(result, "home.progress")  # причина сбоя — в лог
            return Block.unavailable()
        return Block.ok(
            HomeProgressCard(
                level=progress.level,
                xp_in_level=progress.xp_in_level,
                xp_to_next=progress.xp_to_next,
                best_streak=max((s.current for s in progress.streaks), default=0),
                can_spin=wheel.can_spin,
                quests_open=sum(1 for q in quests.daily if not q.completed),
            )
        )


__all__ = ["GetPlayerPage", "GetProfilePage", "GetShellPage", "GetShopPage", "HomeProgress"]
