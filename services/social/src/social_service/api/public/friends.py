"""Друзья, блокировки, приватность, presence, приглашения (5c, поток F1; ADR 0050).

* все ручки - только пользователь с аккаунтом (гость - 403 ``auth.guest_forbidden``);
* список друзей, заявки, код и блокировки видны только владельцу; ответы - ``private, no-store``;
* мутации идемпотентны: ``Idempotency-Key`` у заявок и приглашений, PUT/DELETE - естественно;
* скрытый профиль, заблокированный и несуществующий игрок отвечают одинаково (нет перечисления);
* перебор кодов и ников ограничен лимитом по пользователю и IP (fail-closed: 503/429).

Без ``from __future__ import annotations``: аннотации ручек используют локальный псевдоним
зависимости, FastAPI должен получить его объектом, а не строкой.
"""

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from gv_auth import Principal, Sanction, require_user
from gv_service import IdempotencyKey, problem_responses
from social_service.api.schemas import (
    BlockList,
    BlockView,
    FriendCodeView,
    FriendList,
    FriendRequestCreate,
    FriendRequests,
    FriendRequestView,
    FriendView,
    PlayerRelation,
    PlayerRelationItem,
    PlayerRelations,
    PrivacySettings,
    RoomInviteCreate,
    RoomInviteResult,
)
from social_service.application.friends import FriendsService
from social_service.domain.friends import Privacy

User = Annotated[Principal, Depends(require_user)]
UserIdPath = Annotated[UUID, Path(description="Идентификатор игрока")]
RequestIdPath = Annotated[UUID, Path(description="Идентификатор заявки")]
PRIVATE = "private, no-store"
MAX_RELATIONS_BATCH = 50
"""Предел пакета `players/relations` (bff режет запросы по `GV_PAGES__RELATIONS_BATCH_SIZE`)."""

type ServiceFactory = Callable[[], FriendsService]


def _private(response: Response) -> None:
    response.headers["Cache-Control"] = PRIVATE


def register_friend_routes(router: APIRouter, factory: ServiceFactory) -> None:
    """Добавить в ``/v1`` ручки друзей, блокировок, приватности и приглашений.

    ``factory`` вызывается на каждый запрос: use-case собирается из актуального контейнера.
    """

    def service() -> FriendsService:
        return factory()

    Svc = Annotated[FriendsService, Depends(service)]  # noqa: N806 - псевдоним типа

    @router.get(
        "/me/friends",
        response_model=FriendList,
        summary="Мои друзья",
        description="Друзья со статусом `online | in_match | offline` (presence, ADR 0050).",
        responses=problem_responses(401, 403),
    )
    async def my_friends(user: User, svc: Svc, response: Response) -> FriendList:
        _private(response)
        items = await svc.friends(user.user_id)
        return FriendList(
            items=[
                FriendView(
                    user_id=i.friend.user_id,
                    nickname=i.friend.nickname,
                    status=i.status,
                    friends_since=i.friend.since,
                )
                for i in items
            ],
            total=len(items),
        )

    @router.delete(
        "/me/friends/{user_id}",
        status_code=204,
        summary="Удалить из друзей",
        description="Событие `FriendRemoved`. Повтор - 204.",
        responses=problem_responses(401, 403),
    )
    async def remove_friend(user_id: UserIdPath, user: User, svc: Svc) -> None:
        await svc.remove_friend(user.user_id, user_id)

    @router.get(
        "/me/friend-requests",
        response_model=FriendRequests,
        summary="Заявки в друзья",
        responses=problem_responses(401, 403),
    )
    async def my_requests(user: User, svc: Svc, response: Response) -> FriendRequests:
        _private(response)
        pending = await svc.requests(user.user_id)

        def view(v: object) -> FriendRequestView:
            return FriendRequestView.model_validate(v, from_attributes=True)

        return FriendRequests(
            incoming=[view(v) for v in pending.incoming],
            outgoing=[view(v) for v in pending.outgoing],
        )

    @router.post(
        "/me/friend-requests",
        response_model=FriendRequestView,
        status_code=201,
        summary="Отправить заявку по нику, коду или id игрока",
        description=(
            "Ровно одно из `nickname`, `code`, `user_id` (иначе 422). Встречная заявка сразу "
            "создаёт дружбу. Ответ для неизвестного ника/кода/id, "
            "скрывшего заявки (`friend_requests = none`) и заблокировавшего игрока "
            "одинаков: 404 `social.player_not_found`. Лимиты из "
            "конфига: 429 `social.friend_requests_daily_limit`, 409 `social.friends_limit`; "
            "409 `social.already_friends`, 409 `social.request_pending`, 403 "
            "`social.blocked_by_you` (себя заблокировал). Повтор по `Idempotency-Key` - та же "
            "заявка."
        ),
        responses=problem_responses(401, 403, 404, 409, 429, 503),
    )
    async def send_request(
        body: FriendRequestCreate,
        user: User,
        key: IdempotencyKey,
        *,
        svc: Svc,
        request: Request,
        response: Response,
    ) -> dict[str, object]:
        _private(response)
        ip = request.client.host if request.client else None
        return await svc.send_request(
            user.user_id,
            key=key,
            nickname=body.nickname,
            code=body.code,
            user_id=body.user_id,
            client_ip=ip,
        )

    @router.post(
        "/me/friend-requests/{request_id}/accept",
        response_model=FriendRequestView,
        summary="Принять заявку",
        description="Событие `FriendAdded`. Принять может только адресат: чужая заявка - 404.",
        responses=problem_responses(401, 403, 404, 409, 503),
    )
    async def accept_request(
        request_id: RequestIdPath, user: User, key: IdempotencyKey, svc: Svc, response: Response
    ) -> dict[str, object]:
        _private(response)
        return await svc.accept(user.user_id, request_id, key=key)

    @router.post(
        "/me/friend-requests/{request_id}/decline",
        status_code=204,
        summary="Отклонить входящую заявку",
        responses=problem_responses(401, 403, 404),
    )
    async def decline_request(
        request_id: RequestIdPath, user: User, key: IdempotencyKey, svc: Svc
    ) -> None:
        await svc.decline(user.user_id, request_id, key=key)

    @router.delete(
        "/me/friend-requests/{request_id}",
        status_code=204,
        summary="Отозвать исходящую заявку",
        responses=problem_responses(401, 403, 404),
    )
    async def cancel_request(request_id: RequestIdPath, user: User, svc: Svc) -> None:
        await svc.cancel(user.user_id, request_id)

    @router.get(
        "/me/friend-code",
        response_model=FriendCodeView,
        summary="Мой код добавления",
        description="8 символов; создаётся при первом запросе.",
        responses=problem_responses(401, 403),
    )
    async def my_code(user: User, svc: Svc, response: Response) -> FriendCodeView:
        _private(response)
        code, rotated_at = await svc.my_code(user.user_id)
        return FriendCodeView.model_validate({"code": code, "rotated_at": rotated_at})

    @router.post(
        "/me/friend-code/regenerate",
        response_model=FriendCodeView,
        summary="Перевыпустить код",
        description="Старый код перестаёт действовать (404 при использовании).",
        responses=problem_responses(401, 403),
    )
    async def regenerate_code(
        user: User, key: IdempotencyKey, svc: Svc, response: Response
    ) -> dict[str, object]:
        _private(response)
        return await svc.regenerate_code(user.user_id, key=key)

    @router.get(
        "/me/blocks",
        response_model=BlockList,
        summary="Мои блокировки",
        responses=problem_responses(401, 403),
    )
    async def my_blocks(user: User, svc: Svc, response: Response) -> BlockList:
        _private(response)
        rows = await svc.blocks(user.user_id)
        return BlockList(
            items=[
                BlockView(user_id=r.user_id, nickname=r.nickname, created_at=r.created_at)
                for r in rows
            ]
        )

    @router.put(
        "/me/blocks/{user_id}",
        status_code=204,
        summary="Заблокировать игрока",
        description=(
            "Рвёт дружбу и заявки, запрещает заявки и приглашения в обе стороны, скрывает "
            "профиль. События `FriendRemoved` (если была дружба) и `UserBlocked`. Повтор - 204; "
            "себя заблокировать нельзя: 422 `social.cannot_block_self`."
        ),
        responses=problem_responses(401, 403, 404, 422),
    )
    async def block(user_id: UserIdPath, user: User, svc: Svc) -> None:
        await svc.block(user.user_id, user_id)

    @router.delete(
        "/me/blocks/{user_id}",
        status_code=204,
        summary="Снять блокировку",
        description="Дружба не восстанавливается. Повтор - 204.",
        responses=problem_responses(401, 403),
    )
    async def unblock(user_id: UserIdPath, user: User, svc: Svc) -> None:
        await svc.unblock(user.user_id, user_id)

    @router.get(
        "/me/privacy",
        response_model=PrivacySettings,
        summary="Настройки приватности",
        description="По умолчанию: профиль и статистика публичны, заявки принимаются от всех.",
        responses=problem_responses(401, 403),
    )
    async def get_privacy(user: User, svc: Svc, response: Response) -> PrivacySettings:
        _private(response)
        value = await svc.privacy(user.user_id)
        return PrivacySettings(
            profile_public=value.profile_public,
            stats_public=value.stats_public,
            friend_requests=value.friend_requests,
        )

    @router.put(
        "/me/privacy",
        response_model=PrivacySettings,
        summary="Изменить настройки приватности",
        description="Заменяет целиком. История матчей всегда только владельцу и не настраивается.",
        responses=problem_responses(401, 403, 422),
    )
    async def put_privacy(
        body: PrivacySettings, user: User, svc: Svc, response: Response
    ) -> PrivacySettings:
        _private(response)
        await svc.set_privacy(
            user.user_id, Privacy(body.profile_public, body.stats_public, body.friend_requests)
        )
        return body

    @router.post(
        "/me/presence",
        status_code=204,
        summary="Heartbeat присутствия",
        description=(
            "Фронтенд шлёт раз в `presence.heartbeat_s`; ключ в Redis живёт `presence.ttl_s`. "
            "Статус «в матче» берётся из событий match. Лимит по пользователю."
        ),
        responses=problem_responses(401, 403, 429, 503),
    )
    async def heartbeat(user: User, svc: Svc) -> None:
        await svc.heartbeat(user.user_id)

    @router.post(
        "/me/friends/{user_id}/invite",
        response_model=RoomInviteResult,
        status_code=202,
        summary="Пригласить друга в комнату",
        description=(
            "Событие `RoomInviteSent` -> уведомление `room_invite` (in-app и realtime). "
            "Код комнаты здесь не проверяется: проверка при входе в match. Только друг; "
            "иначе, при блокировке в любую сторону или санкции `ONLINE`, - 403 "
            "`social.invite_forbidden`. Лимит в минуту: 429."
        ),
        responses=problem_responses(401, 403, 404, 429, 503),
    )
    async def invite(
        user_id: UserIdPath,
        body: RoomInviteCreate,
        user: User,
        key: IdempotencyKey,
        svc: Svc,
    ) -> RoomInviteResult:
        outcome = await svc.invite(
            user.user_id,
            user_id,
            body.room_code,
            key=key,
            online_banned=user.is_sanctioned(Sanction.ONLINE),
        )
        return RoomInviteResult(invite_id=outcome.invite_id, repeated=outcome.repeated)

    @router.get(
        "/players/relations",
        response_model=PlayerRelations,
        summary="Видимость нескольких игроков для зрителя",
        description=(
            "Пакетный `relation` для bff (итог матча: можно ли звать в друзья): до "
            f"{MAX_RELATIONS_BATCH} игроков за вызов, дубли схлопываются, порядок как в запросе. "
            "Скрытый профиль, блокировка и неизвестный игрок дают одинаковый ответ. "
            "Лимит на пользователя (`GV_SOCIAL__RELATIONS_PER_MINUTE`): 429 + `Retry-After`."
        ),
        responses=problem_responses(401, 403, 422, 429, 503),
    )
    async def relations(
        user: User,
        svc: Svc,
        response: Response,
        user_ids: Annotated[list[UUID], Query(min_length=1, max_length=MAX_RELATIONS_BATCH)],
    ) -> PlayerRelations:
        _private(response)
        values = await svc.relations(user.user_id, user_ids)
        return PlayerRelations(
            items=[
                PlayerRelationItem(
                    user_id=target,
                    profile_visible=v.profile_visible,
                    stats_visible=v.stats_visible,
                    is_friend=v.is_friend,
                    can_send_request=v.can_send_request,
                    blocked_by_me=v.blocked_by_me,
                    nickname=v.nickname,
                )
                for target, v in values
            ]
        )

    @router.get(
        "/players/{user_id}/relation",
        response_model=PlayerRelation,
        summary="Видимость игрока для зрителя",
        description=(
            "Для bff: показывать ли профиль и статистику, можно ли звать в друзья. Скрытый "
            "профиль, блокировка и неизвестный игрок дают одинаковый ответ (все `false`)."
        ),
        responses=problem_responses(401, 403),
    )
    async def relation(
        user_id: UserIdPath, user: User, svc: Svc, response: Response
    ) -> PlayerRelation:
        _private(response)
        value = await svc.relation(user.user_id, user_id)
        return PlayerRelation(
            profile_visible=value.profile_visible,
            stats_visible=value.stats_visible,
            is_friend=value.is_friend,
            can_send_request=value.can_send_request,
            blocked_by_me=value.blocked_by_me,
            nickname=value.nickname,
        )
