"""Публичный счётчик «игроков сейчас» (без авторизации; число не персональное).

Без ``from __future__ import annotations``: FastAPI получает аннотации объектами.
"""

from collections.abc import Callable

from fastapi import APIRouter, Request, Response

from gv_service import problem_responses
from social_service.api.schemas import OnlineCount
from social_service.application.online_count import OnlineCounter

type CounterFactory = Callable[[], OnlineCounter]


def register_online_routes(router: APIRouter, counter: CounterFactory) -> None:
    """Добавить в ``/v1`` публичную ручку счётчика онлайна."""

    @router.get(
        "/presence/online-count",
        response_model=OnlineCount,
        summary="Сколько игроков онлайн",
        description=(
            "Игроки с аккаунтом, приславшие heartbeat за последние `presence.ttl_s` секунд; "
            "гости не считаются. Публичная ручка без токена: число кэшируется в процессе "
            "(`GV_SOCIAL__ONLINE_COUNT_CACHE_S`), чтения с одного IP ограничены "
            "(`GV_SOCIAL__ONLINE_COUNT_PER_MINUTE`, 429 + `Retry-After`). Redis недоступен - 503."
        ),
        responses=problem_responses(429, 503),
    )
    async def online_count(request: Request, response: Response) -> OnlineCount:
        online = counter()
        response.headers["Cache-Control"] = f"public, max-age={online.cache_s}"
        ip = request.client.host if request.client else None
        return OnlineCount(count=await online.count(ip))
