"""Фикстуры тестов rating. Инфраструктура и фейки — из ``gv_testkit``.

Здесь — сборка сервиса на реальных Postgres и Redis (``rating``): HTTP-клиент, доставка
событий через настоящий ``EventProcessor`` (inbox, транзакция), запуск периодических задач
и помощники сценариев (ladder из примера контракта, матч, токены).
"""

from __future__ import annotations

import copy
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr
from rating_service.infrastructure.container import RatingContainer, container_for
from rating_service.infrastructure.db.tables import TABLES
from rating_service.main import DEFINITION
from rating_service.settings import Settings

from gv_core.ids import uuid7
from gv_messaging import EventEnvelope, EventProcessor, Inbox, Outcome
from gv_service import Infrastructure, JobResult, create_app
from gv_testkit import (
    FakeClock,
    FakeDeadLetterSink,
    InfraProvider,
    TestTokenIssuer,
    apply_migrations,
    load_example,
    outbox_envelopes,
    run_periodic_job,
    service_client,
)

EXAMPLES = Path(__file__).resolve().parents[3] / "contracts" / "rating" / "examples"
# Пятница 12:00 UTC; неделя топа начинается в понедельник 2026-10-19 (Europe/Moscow).
START = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
SEED_ACTOR = UUID(int=1)


def ladder_config(key: str = "film", **patch: Any) -> dict[str, Any]:
    """Конфиг ladder-а из примера контракта; ``patch`` — вложенные правки через ``__``."""
    raw: dict[str, Any] = copy.deepcopy(
        json.loads((EXAMPLES / "ladder.film.v1.json").read_text(encoding="utf-8"))
    )
    raw["key"] = key
    raw["domain"] = key if key in ("music", "film") else raw["domain"]
    for dotted, value in patch.items():
        target = raw
        *head, last = dotted.split("__")
        for part in head:
            target = target[part]
        target[last] = value
    return raw


@dataclass
class RatingService:
    """Сервис на реальных Postgres и Redis."""

    client: httpx.AsyncClient
    infra: Infrastructure
    container: RatingContainer
    processor: EventProcessor
    issuer: TestTokenIssuer
    clock: FakeClock
    # Один и тот же админ во всех запросах теста: ключи идемпотентности привязаны к актору.
    admin_id: UUID = field(default_factory=uuid7)

    @staticmethod
    def config(key: str = "film", **patch: Any) -> dict[str, Any]:
        """Конфиг ladder-а из примера контракта (правки — вложенные ключи через ``__``)."""
        return ladder_config(key, **patch)

    async def deliver(self, envelope: EventEnvelope) -> Outcome:
        return await self.processor.process(envelope.to_bytes(), source="test")

    async def run_job(self, name: str) -> JobResult:
        return await run_periodic_job(DEFINITION, name, self.infra)

    async def outbox(self, event_type: str | None = None) -> list[EventEnvelope]:
        assert self.infra.sessions is not None
        async with self.infra.sessions() as session:
            events = await outbox_envelopes(session, TABLES.outbox)
        return [e for e in events if event_type is None or e.event_type == event_type]

    def user(self, user_id: UUID | None = None) -> dict[str, str]:
        token = self.issuer.issue(user_id=user_id or uuid7())
        return {"Authorization": f"Bearer {token}"}

    def admin(self, *perms: str) -> dict[str, str]:
        token = self.issuer.issue(user_id=self.admin_id, perms=("admin.access", *perms))
        return {"Authorization": f"Bearer {token}"}

    async def seed(self, key: str = "film", **patch: Any) -> UUID:
        """Ladder из сида (версия 1 опубликована, первый сезон начат); id сезона."""
        created = await self.container.ladder_commands.bootstrap(
            ladder_config(key, **patch), actor_id=SEED_ACTOR, reason="тестовый сид"
        )
        assert created
        async with self.container.uow_factory()() as uow:
            season = await uow.seasons.active(key)
        assert season is not None
        return season.season_id

    async def profile(self, user_id: UUID, nickname: str, version: int = 1) -> None:
        base = load_example("identity.UserRegistered")
        payload = {
            **base.payload,
            "user_id": str(user_id),
            "nickname": nickname,
            "version": version,
        }
        event = base.model_copy(update={"aggregate_id": str(user_id), "payload": payload})
        assert await self.deliver(event) is Outcome.PROCESSED

    def match_event(
        self,
        winner: UUID,
        loser: UUID,
        *,
        ladder: str = "film",
        queue_format: str | None = "classic",
        draw: bool = False,
        loser_finish: str = "completed",
        **override: Any,
    ) -> EventEnvelope:
        """``match.MatchFinished`` рейтинговой дуэли (по умолчанию: ``winner`` выиграл)."""
        base = load_example("match.MatchFinished")
        match_id = uuid7()
        players = [
            {
                "user_id": str(winner),
                "slot": 0,
                "score": 10,
                "place": 1,
                "outcome": "draw" if draw else "win",
                "finish": "completed",
            },
            {
                "user_id": str(loser),
                "slot": 1,
                "score": 5,
                "place": 2,
                "outcome": "draw" if draw else "loss",
                "finish": loser_finish,
            },
        ]
        payload = {
            **base.payload,
            "match_id": str(match_id),
            "format": "duel",
            "ranked": True,
            "aborted": False,
            "ladder": ladder,
            "queue_format": queue_format,
            "players": players,
            "finished_at": self.clock.now().isoformat(),
            **override,
        }
        return base.model_copy(update={"aggregate_id": str(match_id), "payload": payload})

    async def play(self, winner: UUID, loser: UUID, **kwargs: Any) -> EventEnvelope:
        """Доставить матч и вернуть событие (проверено, что обработано)."""
        event = self.match_event(winner, loser, **kwargs)
        assert await self.deliver(event) is Outcome.PROCESSED
        return event

    async def rating_of(self, user_id: UUID, ladder: str = "film") -> dict[str, Any]:
        """Строка ``me/ratings`` пользователя по ladder-у."""
        response = await self.client.get("/v1/me/ratings", headers=self.user(user_id))
        assert response.status_code == 200, response.text
        return next(i for i in response.json()["items"] if i["ladder"] == ladder)


@pytest.fixture
def fake_clock_start() -> FakeClock:
    """Часы сервиса: пятница 2026-10-23 12:00 UTC."""
    return FakeClock(START)


@pytest.fixture
async def rating(
    database_url: str,
    infra: InfraProvider,
    token_issuer: TestTokenIssuer,
    fake_clock_start: FakeClock,
) -> AsyncIterator[RatingService]:
    """Сервис rating: реальные Postgres и Redis, события через EventProcessor."""
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    settings = Settings(
        database_url=SecretStr(database_url), redis_url=SecretStr(infra.redis_url())
    )
    runtime = await Infrastructure.open(settings, TABLES, clock=fake_clock_start)
    container = container_for(runtime)
    assert runtime.sessions is not None
    assert DEFINITION.build_event_handlers is not None
    processor = EventProcessor(
        group="rating.consumer",
        registry=DEFINITION.build_event_handlers(runtime),
        session_factory=runtime.sessions,
        inbox=Inbox(TABLES.inbox),
        dead_letters=FakeDeadLetterSink(),
        clock=fake_clock_start,
    )
    app = create_app(DEFINITION, runtime, jwt_verifier=token_issuer.verifier())
    try:
        async with service_client(app) as client:
            yield RatingService(
                client, runtime, container, processor, token_issuer, fake_clock_start
            )
    finally:
        await runtime.close()
