"""Контракты 5d: ответы ручек событий против снимка OpenAPI, события реализации против схем."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator

from gv_core.ids import uuid7
from gv_testkit import assert_event_matches_schema

pytestmark = pytest.mark.contract

Service = Any  # фикстура `progression` описана в conftest.py
SNAPSHOT = json.loads(
    (Path(__file__).resolve().parents[4] / "contracts" / "openapi" / "progression.json").read_text(
        encoding="utf-8"
    )
)
KEY = "autumn"
SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[4]
        / "contracts"
        / "progression"
        / "event_definition.v1.json"
    ).read_text(encoding="utf-8")
)


def conforms(template: str, method: str, response: httpx.Response) -> None:
    """Тело ответа соответствует схеме снимка OpenAPI для его статуса."""
    operation = SNAPSHOT["paths"][template][method.lower()]
    spec = operation["responses"].get(str(response.status_code))
    assert spec is not None, f"{method} {template}: статус {response.status_code} не описан"
    media = "application/problem+json" if response.status_code >= 400 else "application/json"
    schema = spec["content"][media]["schema"]
    validator = Draft202012Validator({**schema, "components": SNAPSHOT["components"]})
    errors = [e.message for e in validator.iter_errors(response.json())]
    assert not errors, f"{method} {template} {response.status_code}: {errors}"


async def test_event_responses_and_events_match_contracts(progression: Service) -> None:
    ev = progression.ev
    await progression.publish_config()
    await progression.add_item("autumn_frame", event_key=KEY)
    body = ev.body(
        ev.find("leaf", 1, ev.trigger("rounds_played", 1)),
        ev.find("fin", 2, final=True),
        item_key="autumn_frame",
    )
    # определение, которое принимает API, соответствует JSON Schema контракта
    assert not list(Draft202012Validator(SCHEMA).iter_errors(body))

    manage, publish = ev.MANAGE, ev.PUBLISH
    created = await progression.create_event(KEY, body)
    conforms("/admin/v1/events", "post", created)
    conforms("/admin/v1/events", "post", await progression.create_event(KEY, body))
    conforms(
        "/admin/v1/events/{event_key}",
        "get",
        await progression.client.get(f"/admin/v1/events/{KEY}", headers=progression.admin(manage)),
    )
    conforms(
        "/admin/v1/events",
        "get",
        await progression.client.get("/admin/v1/events", headers=progression.admin(manage)),
    )
    run_id = await progression.publish_event(KEY, body, create=False)
    # опубликованный запуск: повторная публикация - Problem из снимка
    again = await progression.client.post(
        f"/admin/v1/events/{KEY}/runs/{run_id}/publish",
        headers=progression.admin(publish),
        json={"body_sha256": created.json()["body_sha256"], "reason": "повтор публикации"},
    )
    conforms("/admin/v1/events/{event_key}/runs/{run_id}/publish", "post", again)
    conforms(
        "/admin/v1/events/{event_key}/runs/{run_id}/stats",
        "get",
        await progression.client.get(
            f"/admin/v1/events/{KEY}/runs/{run_id}/stats", headers=progression.admin(manage)
        ),
    )

    user = uuid7()
    conforms("/v1/events/active", "get", await progression.client.get("/v1/events/active"))
    conforms("/v1/events/{event_key}", "get", await progression.get(f"/v1/events/{KEY}", user))
    conforms(
        "/v1/events/{event_key}/finds/{find}/claim",
        "post",
        await progression.claim(user, KEY, "fin"),  # 409: финал закрыт
    )
    env = progression.game_finished(user, [10, 10]).model_copy(
        update={"event_id": uuid7(), "occurred_at": progression.clock.now()}
    )
    await progression.deliver(env)  # серверная находка leaf открыта
    claimed = await progression.claim(user, KEY, "fin")
    conforms("/v1/events/{event_key}/finds/{find}/claim", "post", claimed)
    assert claimed.status_code == 200, claimed.text
    conforms("/v1/me/events", "get", await progression.get("/v1/me/events", user))
    unknown = await progression.claim(user, "nope", "leaf")
    conforms("/v1/events/{event_key}/finds/{find}/claim", "post", unknown)

    stop = await progression.client.post(
        f"/admin/v1/events/{KEY}/runs/{run_id}/stop",
        headers=progression.admin(publish),
        json={"reason": "завершение проверки"},
    )
    conforms("/admin/v1/events/{event_key}/runs/{run_id}/stop", "post", stop)
    progression.clock.advance(timedelta(seconds=1))

    outbox = await progression.outbox()
    types = {e.event_type for e in outbox}
    assert {
        "progression.FindUnlocked",
        "progression.EventCompleted",
        "progression.EventRunFinished",
    } <= types
    for envelope in outbox:
        if envelope.event_type.split(".")[1] in {
            "FindUnlocked",
            "EventCompleted",
            "EventRunFinished",
        }:
            assert_event_matches_schema(envelope)


async def test_tick_started_event_matches_schema(progression: Service) -> None:
    ev = progression.ev
    await progression.publish_config()
    await progression.publish_event(
        KEY,
        ev.body(ev.find("leaf", 1), ev.find("fin", 2, final=True)),
        starts_at=progression.clock.now(),
    )
    await progression.run_job("progression.events.tick")
    started = await progression.outbox("progression.EventRunStarted")
    assert len(started) == 1
    assert_event_matches_schema(started[0])
