"""Клиенты game и media-pipeline (R6, D5, D11, ADR 0033).

* game ``POST /internal/v1/round-plans`` — план матча (scope ``rounds.plan``), один вызов
  при старте; ``GET /internal/v1/modes/{key}/versions/{v}`` — тело конфига шаблона (версии
  неизменяемы: кэш в процессе без срока);
* media-pipeline ``POST /internal/v1/asset-links`` — ссылка уровня (scope ``assets.link``),
  субъект — ``room:{code}`` (как ``sess:<id>`` у game).

Сервисный токен — ``gv_service.build_service_token_provider`` (``GV_SERVICE_AUTH__*``); после
401 кэш токена сбрасывается. Таймаут на вызов, без ретраев (старт ждёт ответа владельца
≤ ``GV_MATCH__REPLY_TIMEOUT_MS``), circuit breaker. Отказ game — 503
``match.game_unavailable`` («онлайн временно недоступен»), комната остаётся в лобби.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

import httpx
import structlog
from prometheus_client import Counter

from gv_core.errors import AppError, DependencyUnavailableError, GoneError
from gv_game_kernel import AssetRef, ClueAsset
from match_service.application.ports import RoundPlanRequest
from match_service.domain import errors
from match_service.domain.errors import (
    MatchInvalidError,
    MatchNotFoundError,
    MatchUnavailableError,
)
from match_service.domain.model import (
    AnswerKey,
    LevelAsset,
    PersonKey,
    PlannedRound,
    PlanOption,
    RoundPlan,
)
from match_service.infrastructure.breaker import CircuitBreaker

log = structlog.get_logger(__name__)

CALLS = Counter(
    "gv_match_dependency_calls_total", "Вызовы game и media-pipeline", ["dep", "result"]
)

PLANS_PATH = "/internal/v1/round-plans"
LINKS_PATH = "/internal/v1/asset-links"
_GONE = frozenset({404, 410})

# Ошибки game → коды match (контракт ручки round-plans).
_GAME_ERRORS: dict[str, tuple[type[AppError], str]] = {
    "game.mode_not_found": (MatchNotFoundError, errors.MODE_NOT_FOUND),
    "game.mode_not_online": (MatchInvalidError, errors.MODE_NOT_ONLINE),
    "game.collection_not_found": (MatchNotFoundError, errors.COLLECTION_NOT_FOUND),
    "game.collection_required": (MatchInvalidError, errors.COLLECTION_REQUIRED),
    "game.collection_domain_mismatch": (MatchInvalidError, errors.COLLECTION_NOT_FOUND),
    "game.too_many_collections": (MatchInvalidError, errors.TOO_MANY_COLLECTIONS),
    "game.pool_too_small": (MatchInvalidError, errors.POOL_TOO_SMALL),
}


class TokenProvider(Protocol):
    """Сервисный токен (``gv_auth.ServiceTokenProvider``)."""

    async def token(self) -> str:
        """Действующий токен."""
        ...

    def invalidate(self) -> None:
        """Забыть токен (ответ 401)."""
        ...


class UnconfiguredTokens:
    """``GV_SERVICE_AUTH__*`` не заданы: вызовы internal-ручек — 503."""

    async def token(self) -> str:
        """Всегда отказ."""
        raise MatchUnavailableError("сервисная аутентификация match не настроена")

    def invalidate(self) -> None:
        """Кэша нет."""


class _Client:
    def __init__(
        self,
        name: str,
        base_url: str,
        tokens: TokenProvider,
        *,
        timeout_s: float,
        breaker: CircuitBreaker,
        client: httpx.AsyncClient | None,
    ) -> None:
        self._name = name
        self._tokens = tokens
        self._breaker = breaker
        self._http = client or httpx.AsyncClient(base_url=base_url, timeout=timeout_s)

    async def aclose(self) -> None:
        """Закрыть HTTP-клиент."""
        await self._http.aclose()

    async def _call(self, method: str, path: str, json: Any = None) -> httpx.Response:
        if self._breaker.is_open:
            CALLS.labels(self._name, "breaker_open").inc()
            raise self._unavailable()
        token = await self._tokens.token()
        try:
            response = await self._http.request(
                method, path, json=json, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.HTTPError as exc:
            self._breaker.failure()
            CALLS.labels(self._name, "network").inc()
            log.warning("match.dependency_network_error", dep=self._name, error=type(exc).__name__)
            raise self._unavailable() from exc
        if response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR:
            self._breaker.failure()
            CALLS.labels(self._name, "server_error").inc()
            raise self._unavailable()
        self._breaker.success()
        if response.status_code == httpx.codes.UNAUTHORIZED:
            self._tokens.invalidate()
        CALLS.labels(self._name, str(response.status_code)).inc()
        return response

    def _unavailable(self) -> AppError:
        raise NotImplementedError


class GameClient(_Client):
    """``RoundPlanPort`` и тела конфигов через internal API game."""

    def __init__(
        self,
        base_url: str,
        tokens: TokenProvider,
        *,
        timeout_s: float,
        breaker: CircuitBreaker,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            "game", base_url, tokens, timeout_s=timeout_s, breaker=breaker, client=client
        )
        self._bodies: dict[tuple[str, int], dict[str, Any]] = {}  # Any: тело ModeConfig

    def _unavailable(self) -> AppError:
        return MatchUnavailableError("онлайн временно недоступен", code=errors.GAME_UNAVAILABLE)

    async def create_round_plan(self, request: RoundPlanRequest) -> RoundPlan:
        """План матча (D5).

        Raises:
            MatchInvalidError | MatchNotFoundError: ошибки плана (``match.pool_too_small`` …).
            MatchUnavailableError: game недоступен.
        """
        response = await self._call(
            "POST",
            PLANS_PATH,
            {
                "mode_key": request.mode_key,
                "version": request.version,
                "seed": request.seed,
                "rounds": request.rounds,
                "collection_ids": [str(c) for c in request.collection_ids],
                "exclude_ids": [str(x) for x in request.exclude_ids],
            },
        )
        if response.status_code != httpx.codes.OK:
            raise self._plan_error(response)
        try:
            return parse_plan(response.json())
        except (KeyError, TypeError, ValueError) as exc:
            log.exception("match.plan_unparsable")
            raise self._unavailable() from exc

    def _plan_error(self, response: httpx.Response) -> AppError:
        try:
            body = response.json()
        except ValueError:
            body = {}
        code = str(body.get("code", "")) if isinstance(body, dict) else ""
        mapped = _GAME_ERRORS.get(code)
        if mapped is None:
            log.warning("match.plan_rejected", status=response.status_code, code=code)
            return self._unavailable()
        cls, match_code = mapped
        details = body.get("details") if isinstance(body.get("details"), dict) else None
        return cls("план матча не получен", code=match_code, details=details)

    async def body(self, key: str, version: int) -> dict[str, Any] | None:  # Any: ModeConfig
        """Тело версии конфига (кэш без срока: версии неизменяемы)."""
        cached = self._bodies.get((key, version))
        if cached is not None:
            return cached
        response = await self._call("GET", f"/internal/v1/modes/{key}/versions/{version}")
        if response.status_code == httpx.codes.NOT_FOUND:
            return None
        if response.status_code != httpx.codes.OK:
            raise self._unavailable()
        body = dict(response.json()["body"])
        self._bodies[key, version] = body
        return body


def parse_plan(data: dict[str, Any]) -> RoundPlan:  # Any: JSON ответа RoundPlan
    """``RoundPlan`` game → план домена."""
    mode = data["mode"]
    rounds = []
    for r in data["rounds"]:
        a = r["answer"]
        rounds.append(
            PlannedRound(
                index=int(r["index"]),
                answer=AnswerKey(
                    media_item_id=UUID(a["media_item_id"]),
                    domain=str(a["domain"]),
                    kind=str(a["kind"]),
                    title=str(a["title"]),
                    year=a.get("year"),
                    aliases=tuple(a.get("aliases", [])),
                    title_aliases=tuple(a.get("title_aliases", [])),
                    persons=tuple(
                        PersonKey(
                            person_id=UUID(p["person_id"]),
                            name=str(p["name"]),
                            normalized=str(p["normalized"]),
                            role=str(p["role"]),
                        )
                        for p in a.get("persons", [])
                    ),
                    partial=tuple(a.get("partial", [])),
                ),
                levels=tuple(
                    LevelAsset(level=int(x["level"]), asset_set_id=UUID(x["asset_set_id"]))
                    for x in r["levels"]
                ),
                options=tuple(
                    PlanOption(
                        option_id=str(o["option_id"]),
                        label_key=o.get("label_key"),
                        media_item_id=UUID(o["media_item_id"]) if o.get("media_item_id") else None,
                        title=o.get("title"),
                    )
                    for o in r.get("options", [])
                ),
                reveal_asset_set_id=(
                    UUID(r["reveal_asset_set_id"]) if r.get("reveal_asset_set_id") else None
                ),
            )
        )
    return RoundPlan(
        mode_version=int(mode["version"]),
        body=dict(mode["body"]),
        seed=str(data["seed"]),
        rounds=tuple(rounds),
        collections=tuple((UUID(c["id"]), int(c["version"])) for c in data.get("collections", [])),
    )


class MediaLinks(_Client):
    """``AssetPort`` ядра: ссылка уровня у media-pipeline (D11)."""

    def __init__(
        self,
        base_url: str,
        tokens: TokenProvider,
        *,
        timeout_s: float,
        breaker: CircuitBreaker,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            "media_pipeline", base_url, tokens, timeout_s=timeout_s, breaker=breaker, client=client
        )

    def _unavailable(self) -> AppError:
        return DependencyUnavailableError(
            "media-pipeline временно недоступен", code="match.assets_unavailable"
        )

    async def link(self, asset: AssetRef, level: int, subject: str) -> ClueAsset:
        """Ссылка на уровень (404/410 — ``GoneError``: раунд ``void``)."""
        response = await self._call(
            "POST",
            LINKS_PATH,
            {"asset_set_id": str(asset.asset_set_id), "level": level, "subject": subject},
        )
        if response.status_code in _GONE:
            raise GoneError("ассет уровня недоступен")
        if response.status_code != httpx.codes.OK:
            log.warning("match.media_rejected", status=response.status_code)
            raise self._unavailable()
        try:
            body = response.json()
            return ClueAsset(
                level=int(body["level"]),
                url=str(body["url"]),
                expires_at=datetime.fromisoformat(body["expires_at"]),
                mime=str(body["mime"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            log.exception("match.media_unparsable")
            raise self._unavailable() from exc
