"""Друзья, блокировки, приватность, presence, приглашения (5c, поток F1; ADR 0050).

Шаг 0a: ручки объявлены, реализация - поток F1; до тех пор отвечают 501
``common.not_implemented``.

* все ручки - только пользователь с аккаунтом (гость - 403 ``auth.guest_forbidden``);
* список друзей, заявки, код и блокировки видны только владельцу; ответы - ``private, no-store``;
* мутации идемпотентны: ``Idempotency-Key`` у заявок и приглашений, PUT/DELETE - естественно;
* скрытый профиль, заблокированный и несуществующий игрок отвечают одинаково (нет перечисления);
* перебор кодов и ников ограничен лимитом по пользователю и IP (fail-closed: 503/429).
"""

# Аргументы ручек - зависимости FastAPI, часть нужна только ради проверки доступа.
# ruff: noqa: ARG001
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path

from gv_auth import Principal, require_user
from gv_core.errors import NotImplementedYetError
from gv_service import IdempotencyKey, problem_responses
from social_service.api.schemas import (
    BlockList,
    FriendCodeView,
    FriendList,
    FriendRequestCreate,
    FriendRequests,
    FriendRequestView,
    PlayerRelation,
    PrivacySettings,
    RoomInviteCreate,
    RoomInviteResult,
)

User = Annotated[Principal, Depends(require_user)]
UserIdPath = Annotated[UUID, Path(description="Идентификатор игрока")]
RequestIdPath = Annotated[UUID, Path(description="Идентификатор заявки")]


def _stub() -> NotImplementedYetError:
    return NotImplementedYetError("ручка появится в потоке F1 (5c)")


def register_friend_routes(router: APIRouter) -> None:
    """Добавить в ``/v1`` ручки друзей, блокировок, приватности и приглашений."""

    @router.get(
        "/me/friends",
        response_model=FriendList,
        summary="Мои друзья",
        description="Друзья со статусом `online | in_match | offline` (presence, ADR 0050).",
        responses=problem_responses(401, 403, 501),
    )
    async def my_friends(user: User) -> FriendList:
        raise _stub()

    @router.delete(
        "/me/friends/{user_id}",
        status_code=204,
        summary="Удалить из друзей",
        description="Событие `FriendRemoved`. Повтор - 204.",
        responses=problem_responses(401, 403, 501),
    )
    async def remove_friend(user_id: UserIdPath, user: User) -> None:
        raise _stub()

    @router.get(
        "/me/friend-requests",
        response_model=FriendRequests,
        summary="Заявки в друзья",
        responses=problem_responses(401, 403, 501),
    )
    async def my_requests(user: User) -> FriendRequests:
        raise _stub()

    @router.post(
        "/me/friend-requests",
        response_model=FriendRequestView,
        status_code=201,
        summary="Отправить заявку по нику или коду",
        description=(
            "Ровно одно из `nickname`, `code`. Встречная заявка сразу создаёт дружбу. Ответ "
            "для неизвестного ника/кода, скрывшего заявки (`friend_requests = none`) и "
            "заблокировавшего игрока одинаков: 404 `social.player_not_found`. Лимиты из "
            "конфига: 429 `social.friend_requests_daily_limit`, 409 `social.friends_limit`; "
            "409 `social.already_friends`, 409 `social.request_pending`, 403 "
            "`social.blocked_by_you` (себя заблокировал). Повтор по `Idempotency-Key` - та же "
            "заявка."
        ),
        responses=problem_responses(401, 403, 404, 409, 429, 501),
    )
    async def send_request(
        body: FriendRequestCreate, user: User, key: IdempotencyKey
    ) -> FriendRequestView:
        raise _stub()

    @router.post(
        "/me/friend-requests/{request_id}/accept",
        response_model=FriendRequestView,
        summary="Принять заявку",
        description="Событие `FriendAdded`. Принять может только адресат: чужая заявка - 404.",
        responses=problem_responses(401, 403, 404, 409, 501),
    )
    async def accept_request(
        request_id: RequestIdPath, user: User, key: IdempotencyKey
    ) -> FriendRequestView:
        raise _stub()

    @router.post(
        "/me/friend-requests/{request_id}/decline",
        status_code=204,
        summary="Отклонить входящую заявку",
        responses=problem_responses(401, 403, 404, 501),
    )
    async def decline_request(request_id: RequestIdPath, user: User, key: IdempotencyKey) -> None:
        raise _stub()

    @router.delete(
        "/me/friend-requests/{request_id}",
        status_code=204,
        summary="Отозвать исходящую заявку",
        responses=problem_responses(401, 403, 404, 501),
    )
    async def cancel_request(request_id: RequestIdPath, user: User) -> None:
        raise _stub()

    @router.get(
        "/me/friend-code",
        response_model=FriendCodeView,
        summary="Мой код добавления",
        description="8 символов; создаётся при первом запросе.",
        responses=problem_responses(401, 403, 501),
    )
    async def my_code(user: User) -> FriendCodeView:
        raise _stub()

    @router.post(
        "/me/friend-code/regenerate",
        response_model=FriendCodeView,
        summary="Перевыпустить код",
        description="Старый код перестаёт действовать (404 при использовании).",
        responses=problem_responses(401, 403, 501),
    )
    async def regenerate_code(user: User, key: IdempotencyKey) -> FriendCodeView:
        raise _stub()

    @router.get(
        "/me/blocks",
        response_model=BlockList,
        summary="Мои блокировки",
        responses=problem_responses(401, 403, 501),
    )
    async def my_blocks(user: User) -> BlockList:
        raise _stub()

    @router.put(
        "/me/blocks/{user_id}",
        status_code=204,
        summary="Заблокировать игрока",
        description=(
            "Рвёт дружбу и заявки, запрещает заявки и приглашения в обе стороны, скрывает "
            "профиль. События `FriendRemoved` (если была дружба) и `UserBlocked`. Повтор - 204; "
            "себя заблокировать нельзя: 422 `social.cannot_block_self`."
        ),
        responses=problem_responses(401, 403, 404, 422, 501),
    )
    async def block(user_id: UserIdPath, user: User) -> None:
        raise _stub()

    @router.delete(
        "/me/blocks/{user_id}",
        status_code=204,
        summary="Снять блокировку",
        description="Дружба не восстанавливается. Повтор - 204.",
        responses=problem_responses(401, 403, 501),
    )
    async def unblock(user_id: UserIdPath, user: User) -> None:
        raise _stub()

    @router.get(
        "/me/privacy",
        response_model=PrivacySettings,
        summary="Настройки приватности",
        description="По умолчанию: профиль и статистика публичны, заявки принимаются от всех.",
        responses=problem_responses(401, 403, 501),
    )
    async def get_privacy(user: User) -> PrivacySettings:
        raise _stub()

    @router.put(
        "/me/privacy",
        response_model=PrivacySettings,
        summary="Изменить настройки приватности",
        description="Заменяет целиком. История матчей всегда только владельцу и не настраивается.",
        responses=problem_responses(401, 403, 422, 501),
    )
    async def put_privacy(body: PrivacySettings, user: User) -> PrivacySettings:
        raise _stub()

    @router.post(
        "/me/presence",
        status_code=204,
        summary="Heartbeat присутствия",
        description=(
            "Фронтенд шлёт раз в `presence.heartbeat_s`; ключ в Redis живёт `presence.ttl_s`. "
            "Статус «в матче» берётся из событий match. Лимит по пользователю."
        ),
        responses=problem_responses(401, 403, 429, 501),
    )
    async def heartbeat(user: User) -> None:
        raise _stub()

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
        responses=problem_responses(401, 403, 404, 429, 501),
    )
    async def invite(
        user_id: UserIdPath, body: RoomInviteCreate, user: User, key: IdempotencyKey
    ) -> RoomInviteResult:
        raise _stub()

    @router.get(
        "/players/{user_id}/relation",
        response_model=PlayerRelation,
        summary="Видимость игрока для зрителя",
        description=(
            "Для bff: показывать ли профиль и статистику, можно ли звать в друзья. Скрытый "
            "профиль, блокировка и неизвестный игрок дают одинаковый ответ (все `false`)."
        ),
        responses=problem_responses(401, 403, 501),
    )
    async def relation(user_id: UserIdPath, user: User) -> PlayerRelation:
        raise _stub()
