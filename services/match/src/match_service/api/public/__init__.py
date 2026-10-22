"""Публичный API (/v1). На gateway: /api/match/v1/....

Правила (план этапа 4, §2.5, §12, D6, D9, D10, D13, D14):

* все ручки — только пользователь (``require_user``): гость — 403 ``auth.guest_forbidden``,
  без токена — 401; cookie-аутентификация мутаций — Origin и CSRF (``gv_auth``);
* ``POST /rooms`` и ``POST /matches/{id}/rate`` — ``Idempotency-Key``; команды комнаты —
  ``op_id`` в теле, команды фазы — ещё ``expected_version``;
* ответы — ``Cache-Control: no-store``; ошибки — RFC 9457 с кодами ``match.*``;
* команда, на которую владелец комнаты не ответил за ``GV_MATCH__REPLY_TIMEOUT_MS``, —
  202 ``CommandAccepted`` (D6).

Ручки только переводят HTTP в use-cases (``MatchServices``): права в комнате проверяет
владелец комнаты, виды для зрителя строит application (античит §12).
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from gv_auth import Principal, require_user
from gv_service import IdempotencyKey, problem_responses
from match_service.api.common import (
    NoStoreRoute,
    RoomCodePath,
    ServicesProvider,
    actor_of,
    respond,
)
from match_service.api.public.schemas import (
    AwayRequest,
    ChatRequest,
    CommandAccepted,
    CreateRoomRequest,
    Cursor,
    EmoteRequest,
    ForfeitRequest,
    GuessRequest,
    GuessResult,
    JoinRequest,
    LeaveRequest,
    MatchCommandResult,
    MatchResult,
    MyMatchesPage,
    OpenRoomsPage,
    RateRequest,
    ReadyRequest,
    RealtimeSubscription,
    RematchRequest,
    RematchResult,
    ResetMemoryRequest,
    RoomCommandResult,
    RoomState,
    RoomView,
    SettingsOverride,
    SettingsRequest,
    SkipRequest,
    StartRequest,
    TargetPlayerRequest,
    TickRequest,
    TickResult,
)
from match_service.domain.room import SettingsChange

User = Annotated[Principal, Depends(require_user)]
MatchId = Annotated[UUID, Path(description="Идентификатор матча")]
Limit = Annotated[int, Query(ge=1, le=50)]

_ACCEPTED: dict[int | str, dict[str, Any]] = {
    202: {
        "model": CommandAccepted,
        "description": "Команда принята в поток комнаты, владелец не ответил вовремя (D6)",
    }
}
_READ = problem_responses(401, 403, 404, 422, 429, 501)
_ROOM_CMD = {**_ACCEPTED, **problem_responses(401, 403, 404, 409, 422, 429, 501, 503)}
_COMMON_ROOM_ERRORS = (
    "404 `match.room_not_found` (нет комнаты или она закрыта), "
    "403 `match.not_participant` (не в этой комнате), "
    "409 `match.phase_mismatch` (команда не для текущей фазы), "
    "429 `match.rate_limited`, 503 `match.rooms_unavailable`."
)


def _payload(body: BaseModel) -> dict[str, Any]:  # Any: JSON команды
    """Поля команды без ``op_id`` (он уходит отдельно в ``RoomCommand``)."""
    return body.model_dump(mode="json", exclude={"op_id"}, exclude_none=True)


class ServiceMeta(BaseModel):
    """Сведения о сервисе."""

    service: str
    status: str


def build_public_router(services: ServicesProvider) -> APIRouter:
    """Публичные ручки."""
    router = APIRouter(prefix="/v1", tags=["match"], route_class=NoStoreRoute)

    @router.get("/meta", response_model=ServiceMeta, summary="Сведения о сервисе")
    async def meta() -> ServiceMeta:
        return ServiceMeta(service=services().service_name, status="ok")

    # ------------------------------------------------------------------ комнаты
    @router.post(
        "/rooms",
        response_model=RoomView,
        status_code=status.HTTP_201_CREATED,
        summary="Создать комнату",
        description=(
            "Комната по онлайн-шаблону (`session.kind` ∈ duel/party/lobby); создатель — хост "
            "в слоте 0. Переопределения — только из `session.lobby.overridable` (D12). "
            "Ошибки: 404 `match.mode_not_found` (нет опубликованного онлайн-шаблона), "
            "422 `match.mode_not_online`, 422 `match.setting_not_allowed` "
            "(`details.field`), 422 `match.collection_required`, 422 "
            "`match.too_many_collections`, 404 `match.collection_not_found`, 403 "
            "`match.ranked_forbidden` (рейтинговые комнаты — только из очереди), 409 "
            "`match.active_room_conflict` (`details.code` — текущая комната), 429 "
            "`match.rate_limited` (`GV_MATCH__LOBBY_CREATE_PER_USER_PER_HOUR`), 503 "
            "`match.game_unavailable`."
        ),
        responses=problem_responses(401, 403, 404, 409, 422, 429, 501, 503),
    )
    async def create_room(
        body: CreateRoomRequest, user: User, key: IdempotencyKey, request: Request
    ) -> RoomView:
        override = body.settings or SettingsOverride()
        change = SettingsChange(
            rounds=override.rounds,
            level_window_s=override.level_window_s,
            answer_format=override.answer_format,
            clue_type=override.clue_type,
            players_max=override.players_max,
            visibility=override.visibility,
            collection_ids=tuple(body.collection_ids) if body.collection_ids else None,
        )
        view = await services().rooms.create(
            actor_of(user, request),
            mode_key=body.mode_key,
            change=change,
            key=key,
            request=body.model_dump(mode="json"),
        )
        return RoomView.model_validate(view)

    @router.get(
        "/rooms/open",
        response_model=OpenRoomsPage,
        summary="Открытые лобби",
        description="Публичные комнаты в фазе `lobby` со свободными местами, новые первыми.",
        responses=problem_responses(401, 403, 422, 501),
    )
    async def open_rooms(
        user: User,  # noqa: ARG001 — зависимость авторизации (только пользователь)
        cursor: Annotated[Cursor | None, Query()] = None,
        limit: Limit = 20,
    ) -> OpenRoomsPage:
        return OpenRoomsPage.model_validate(await services().room_queries.open_rooms(cursor, limit))

    @router.get(
        "/rooms/{code}",
        response_model=RoomView,
        summary="Комната по коду",
        description=(
            "Экран входа по коду или ссылке: состав, настройки, фаза; без данных матча. "
            "Ошибки: 404 `match.room_not_found`; 429 `match.rate_limited` (перебор кодов — "
            "`GV_MATCH__JOIN_BY_CODE_PER_IP_PER_MIN`, fail-closed)."
        ),
        responses=_READ,
    )
    async def get_room(code: RoomCodePath, user: User, request: Request) -> RoomView:
        return RoomView.model_validate(
            await services().room_queries.get(actor_of(user, request), code)
        )

    @router.post(
        "/rooms/{code}/join",
        response_model=RoomCommandResult,
        summary="Войти в комнату",
        description=(
            "Повторный вход участника (реконнект) — тот же слот. Ошибки: 409 "
            "`match.room_full`, 409 `match.active_room_conflict` (`details.code`), 409 "
            "`match.match_in_progress` (матч идёт, а игрок не участник), 403 "
            "`match.kicked` (кик — возврат только по новому приглашению хоста). "
            + _COMMON_ROOM_ERRORS
        ),
        responses=_ROOM_CMD,
    )
    async def join(
        code: RoomCodePath, body: JoinRequest, user: User, request: Request
    ) -> RoomCommandResult | JSONResponse:
        outcome = await services().rooms.join(actor_of(user, request), code, body.op_id)
        return respond(outcome, RoomCommandResult)

    @router.post(
        "/rooms/{code}/leave",
        response_model=RoomCommandResult,
        summary="Выйти из комнаты",
        description=(
            "Уход хоста передаёт хоста следующему по слоту; во время матча — выбывание "
            "(`finish = left`), очки сохраняются. " + _COMMON_ROOM_ERRORS
        ),
        responses=_ROOM_CMD,
    )
    async def leave(
        code: RoomCodePath, body: LeaveRequest, user: User
    ) -> RoomCommandResult | JSONResponse:
        outcome = await services().rooms.command(actor_of(user), code, "leave", body.op_id, {})
        return respond(outcome, RoomCommandResult)

    @router.post(
        "/rooms/{code}/ready",
        response_model=RoomCommandResult,
        summary="Готовность в лобби",
        description=_COMMON_ROOM_ERRORS,
        responses=_ROOM_CMD,
    )
    async def ready(
        code: RoomCodePath, body: ReadyRequest, user: User
    ) -> RoomCommandResult | JSONResponse:
        outcome = await services().rooms.command(
            actor_of(user), code, "ready", body.op_id, {"ready": body.ready}
        )
        return respond(outcome, RoomCommandResult)

    @router.post(
        "/rooms/{code}/settings",
        response_model=RoomCommandResult,
        summary="Настройки лобби (хост)",
        description=(
            "Только в фазе `lobby`; сбрасывает готовность всех. Ошибки: 403 "
            "`match.not_host`, 422 `match.setting_not_allowed` (`details.field`), 422 "
            "`match.too_many_collections`, 404 `match.collection_not_found`, 409 "
            "`match.players_over_limit` (новый `players_max` меньше числа игроков). "
            + _COMMON_ROOM_ERRORS
        ),
        responses=_ROOM_CMD,
    )
    async def change_settings(
        code: RoomCodePath, body: SettingsRequest, user: User
    ) -> RoomCommandResult | JSONResponse:
        outcome = await services().rooms.command(
            actor_of(user),
            code,
            "settings",
            body.op_id,
            body.model_dump(mode="json", include={"settings", "collection_ids"}, exclude_none=True),
        )
        return respond(outcome, RoomCommandResult)

    @router.post(
        "/rooms/{code}/kick",
        response_model=RoomCommandResult,
        summary="Исключить игрока (хост)",
        description=(
            "Только в фазе `lobby`. Ошибки: 403 `match.not_host`, 422 `match.cannot_kick_self`, "
            "404 `match.player_not_found`. " + _COMMON_ROOM_ERRORS
        ),
        responses=_ROOM_CMD,
    )
    async def kick(
        code: RoomCodePath, body: TargetPlayerRequest, user: User
    ) -> RoomCommandResult | JSONResponse:
        outcome = await services().rooms.command(
            actor_of(user), code, "kick", body.op_id, {"user_id": str(body.user_id)}
        )
        return respond(outcome, RoomCommandResult)

    @router.post(
        "/rooms/{code}/transfer-host",
        response_model=RoomCommandResult,
        summary="Передать хоста",
        description=(
            "Ошибки: 403 `match.not_host`, 404 `match.player_not_found`. " + _COMMON_ROOM_ERRORS
        ),
        responses=_ROOM_CMD,
    )
    async def transfer_host(
        code: RoomCodePath, body: TargetPlayerRequest, user: User
    ) -> RoomCommandResult | JSONResponse:
        outcome = await services().rooms.command(
            actor_of(user), code, "transfer_host", body.op_id, {"user_id": str(body.user_id)}
        )
        return respond(outcome, RoomCommandResult)

    @router.post(
        "/rooms/{code}/reset-memory",
        response_model=RoomCommandResult,
        summary="Сбросить память сыгранного (хост)",
        description=(
            "Очищает `exclude_ids` лобби: единицы прошлых матчей снова могут выпасть. "
            "Ошибки: 403 `match.not_host`. " + _COMMON_ROOM_ERRORS
        ),
        responses=_ROOM_CMD,
    )
    async def reset_memory(
        code: RoomCodePath, body: ResetMemoryRequest, user: User
    ) -> RoomCommandResult | JSONResponse:
        outcome = await services().rooms.command(
            actor_of(user), code, "reset_memory", body.op_id, {}
        )
        return respond(outcome, RoomCommandResult)

    @router.post(
        "/rooms/{code}/start",
        response_model=RoomCommandResult,
        summary="Начать матч (хост)",
        description=(
            "План раундов — от game (`POST /internal/v1/round-plans`, D5), затем отсчёт "
            "`countdown_s`. Ошибки: 403 `match.not_host`, 409 `match.not_all_ready`, 409 "
            "`match.not_enough_players`, 422 `match.pool_too_small`, 503 "
            "`match.game_unavailable` (онлайн временно недоступен). " + _COMMON_ROOM_ERRORS
        ),
        responses=_ROOM_CMD,
    )
    async def start(
        code: RoomCodePath, body: StartRequest, user: User
    ) -> RoomCommandResult | JSONResponse:
        outcome = await services().rooms.command(
            actor_of(user), code, "start", body.op_id, {"expected_version": body.expected_version}
        )
        return respond(outcome, RoomCommandResult)

    @router.post(
        "/rooms/{code}/rematch",
        response_model=RematchResult,
        summary="Реванш",
        description=(
            "Только в фазе `summary`: голос за реванш; когда согласны все оставшиеся — "
            "новая комната с теми же настройками (`rematch_code`). " + _COMMON_ROOM_ERRORS
        ),
        responses=_ROOM_CMD,
    )
    async def rematch(
        code: RoomCodePath, body: RematchRequest, user: User
    ) -> RematchResult | JSONResponse:
        outcome = await services().rooms.command(actor_of(user), code, "rematch", body.op_id, {})
        return respond(outcome, RematchResult)

    @router.post(
        "/rooms/{code}/realtime-token",
        response_model=RealtimeSubscription,
        summary="Токен подписки на канал комнаты",
        description=(
            "Subscription-JWT Centrifugo для `room:{code}` — только участнику (D9; отдельный "
            "секрет подписок, публикации клиентов запрещены). Connection-токен — "
            "`POST /api/identity/v1/realtime/token`. Ошибки: 404 `match.room_not_found`, "
            "403 `match.not_participant`, 429 `match.rate_limited`."
        ),
        responses=problem_responses(401, 403, 404, 422, 429, 501),
    )
    async def realtime_token(code: RoomCodePath, user: User) -> RealtimeSubscription:
        return RealtimeSubscription.model_validate(
            await services().room_queries.realtime_token(user.user_id, code)
        )

    @router.get(
        "/rooms/{code}/state",
        response_model=RoomState,
        summary="Снимок комнаты",
        description=(
            "Ресинк после пропуска версии в канале. Снимок фильтруется по зрителю: личное — "
            "только в `me`; до `reveal` нет единицы раунда и ключей ответа. Ошибки: 404 "
            "`match.room_not_found`, 403 `match.not_participant`."
        ),
        responses=_READ,
    )
    async def room_state(code: RoomCodePath, user: User) -> RoomState:
        return RoomState.model_validate(await services().room_queries.state(user.user_id, code))

    @router.post(
        "/rooms/{code}/tick",
        response_model=TickResult,
        summary="Heartbeat и HTTP-фолбэк",
        description=(
            "Раз в `GV_MATCH__TICK_S` (без WebSocket — 1 с): отметка присутствия (D10) и "
            "снимок, если версия новее `known_version`. Ошибки: 404 `match.room_not_found`, "
            "403 `match.not_participant`, 429 `match.rate_limited`."
        ),
        responses=_READ,
    )
    async def tick(code: RoomCodePath, body: TickRequest, user: User) -> TickResult:
        result = await services().room_queries.tick(user.user_id, code, body.known_version)
        return TickResult.model_validate(result)

    # ------------------------------------------------------------------ команды матча
    @router.post(
        "/rooms/{code}/match/guess",
        response_model=GuessResult,
        summary="Ответ",
        description=(
            "Исход своего ответа и оставшиеся попытки; соперники видят только статус. "
            "Ошибки: 409 `match.phase_mismatch` (уровень закрыт, другая `expected_version`), "
            "409 `match.level_locked` (попытки уровня кончились), 422 "
            "`match.answer_kind_unsupported`, 422 `match.option_unknown`. " + _COMMON_ROOM_ERRORS
        ),
        responses=_ROOM_CMD,
    )
    async def guess(
        code: RoomCodePath, body: GuessRequest, user: User
    ) -> GuessResult | JSONResponse:
        outcome = await services().rooms.command(
            actor_of(user),
            code,
            "guess",
            body.op_id,
            body.model_dump(mode="json", exclude={"op_id"}, exclude_none=True),
        )
        return respond(outcome, GuessResult)

    @router.post(
        "/rooms/{code}/match/skip",
        response_model=MatchCommandResult,
        summary="Пропустить уровень",
        description=_COMMON_ROOM_ERRORS,
        responses=_ROOM_CMD,
    )
    async def skip(
        code: RoomCodePath, body: SkipRequest, user: User
    ) -> MatchCommandResult | JSONResponse:
        outcome = await services().rooms.command(
            actor_of(user),
            code,
            "skip",
            body.op_id,
            body.model_dump(mode="json", exclude={"op_id"}),
        )
        return respond(outcome, MatchCommandResult)

    @router.post(
        "/rooms/{code}/match/forfeit",
        response_model=MatchCommandResult,
        summary="Сдаться",
        description=(
            "Дуэль — поражение (`finish = forfeit`), соперник — победа; party — выбывание. "
            + _COMMON_ROOM_ERRORS
        ),
        responses=_ROOM_CMD,
    )
    async def forfeit(
        code: RoomCodePath, body: ForfeitRequest, user: User
    ) -> MatchCommandResult | JSONResponse:
        outcome = await services().rooms.command(actor_of(user), code, "forfeit", body.op_id, {})
        return respond(outcome, MatchCommandResult)

    @router.post(
        "/rooms/{code}/match/emote",
        response_model=MatchCommandResult,
        summary="Эмоция или готовая фраза",
        description=(
            "Ключ — из `chat.emotes` / `chat.phrases` режима. Ошибки: 422 "
            "`match.emote_unknown`, 403 `match.chat_muted` (санкция `CHAT_MUTE`). "
            + _COMMON_ROOM_ERRORS
        ),
        responses=_ROOM_CMD,
    )
    async def emote(
        code: RoomCodePath, body: EmoteRequest, user: User
    ) -> MatchCommandResult | JSONResponse:
        outcome = await services().rooms.emote(
            actor_of(user), code, body.op_id, body.kind, body.key
        )
        return respond(outcome, MatchCommandResult)

    @router.post(
        "/rooms/{code}/match/chat",
        response_model=MatchCommandResult,
        summary="Свободный чат (кастомное лобби)",
        description=(
            "Только при `chat.free_text` (кастомное лобби, D14): нормализация и denylist "
            "`gv_text.ugc` (ADR 0030), ≤ 200 символов, без ссылок. Ошибки: 422 "
            "`match.chat_rejected` (`details.reason`: `denylist` | `link` | `too_long`), "
            "403 `match.chat_disabled` (в режиме нет свободного чата), 403 "
            "`match.chat_muted` (`CHAT_MUTE`), 429 `match.rate_limited` (`CHAT_SLOW`, "
            "`GV_MATCH__CHAT_PER_PLAYER_PER_MIN`). " + _COMMON_ROOM_ERRORS
        ),
        responses=_ROOM_CMD,
    )
    async def chat(
        code: RoomCodePath, body: ChatRequest, user: User
    ) -> MatchCommandResult | JSONResponse:
        outcome = await services().rooms.chat(actor_of(user), code, body.op_id, body.text)
        return respond(outcome, MatchCommandResult)

    @router.post(
        "/rooms/{code}/match/away",
        response_model=MatchCommandResult,
        summary="Отошёл / вернулся",
        description=_COMMON_ROOM_ERRORS,
        responses=_ROOM_CMD,
    )
    async def away(
        code: RoomCodePath, body: AwayRequest, user: User
    ) -> MatchCommandResult | JSONResponse:
        outcome = await services().rooms.command(
            actor_of(user), code, "away", body.op_id, {"away": body.away}
        )
        return respond(outcome, MatchCommandResult)

    # ------------------------------------------------------------------ итоги
    @router.post(
        "/matches/{match_id}/rate",
        status_code=status.HTTP_204_NO_CONTENT,
        summary="Похвалить или пожаловаться",
        description=(
            "Только участник об участнике того же матча, один раз на пару и вид "
            "(`PlayerRated`). Ошибки: 404 `match.match_not_found`, 403 "
            "`match.not_participant`, 422 `match.cannot_rate_self`, 409 "
            "`match.already_rated`, 429 `match.rate_limited`."
        ),
        responses=problem_responses(401, 403, 404, 409, 422, 429, 501),
    )
    async def rate(match_id: MatchId, body: RateRequest, user: User, key: IdempotencyKey) -> None:
        await services().rate.rate(
            user.user_id, match_id, to_user_id=body.to_user_id, kind=body.kind, key=key
        )

    @router.get(
        "/matches/{match_id}",
        response_model=MatchResult,
        summary="Итог матча",
        description=(
            "Участникам и всем пользователям (публичный итог без личных данных). Ошибки: 404 "
            "`match.match_not_found` (нет или ещё идёт)."
        ),
        responses=problem_responses(401, 403, 404, 422, 501),
    )
    async def get_match(match_id: MatchId, user: User) -> MatchResult:  # noqa: ARG001
        return MatchResult.model_validate(await services().matches.get(match_id))

    @router.get(
        "/me/matches",
        response_model=MyMatchesPage,
        summary="Мои матчи",
        description="Сыгранные матчи, новые первыми (keyset-курсор).",
        responses=problem_responses(401, 403, 422, 501),
    )
    async def my_matches(
        user: User,
        cursor: Annotated[Cursor | None, Query()] = None,
        limit: Limit = 20,
    ) -> MyMatchesPage:
        page = await services().matches.mine(user.user_id, cursor, limit)
        return MyMatchesPage.model_validate(page)

    return router
