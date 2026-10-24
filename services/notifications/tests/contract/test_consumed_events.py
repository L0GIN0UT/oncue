"""Контракт потребителя: примеры событий проходят до уведомления, конфиг сверен с контрактом."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from notifications_service.domain.mapping import map_event, matches_filter
from notifications_service.infrastructure.config import load_catalog
from notifications_service.infrastructure.messaging import CONSUMED_TOPICS
from notifications_service.main import DEFINITION

from gv_core.ids import uuid7
from gv_testkit import load_example
from gv_testkit.contracts import load_event_schema, validation_errors

pytestmark = pytest.mark.contract

CONTRACTS = Path(__file__).resolve().parents[4] / "contracts" / "notifications"
PACKAGE_CONFIG = Path(__file__).resolve().parents[2] / "src" / "notifications_service" / "config"


def test_builtin_schema_copy_equals_contract() -> None:
    """Копия схемы внутри пакета (образ без editable) не расходится с контрактом."""
    contract = json.loads((CONTRACTS / "notification_types.v1.json").read_text("utf-8"))
    copy = json.loads((PACKAGE_CONFIG / "notification_types.schema.json").read_text("utf-8"))
    assert copy == contract


def test_builtin_types_cover_the_contract_example_types() -> None:
    """Файл типов 5b содержит все типы примера контракта с теми же правилами маппинга."""
    example = json.loads((CONTRACTS / "examples" / "notification_types.v1.json").read_text("utf-8"))
    catalog = load_catalog()
    for item in example["types"]:
        notification_type = catalog.by_key(item["key"])
        assert notification_type is not None, item["key"]
        assert notification_type.source_event == item["source_event"]
        assert notification_type.recipient == item.get("recipient")
        assert notification_type.audience == item.get("audience")
        assert dict(notification_type.data) == item.get("data", {})
        assert notification_type.dedup_key == item["dedup_key"]
        assert dict(notification_type.filter) == item.get("filter", {})
        assert dict(notification_type.params) == item["params"]
        assert notification_type.user_mutable == item["user_mutable"]


def test_source_events_are_consumed_topics() -> None:
    catalog = load_catalog()
    topics = set()
    for event_type in catalog.source_events:
        schema = load_event_schema(event_type)
        topics.add(schema["x-topic"])
    assert topics <= set(CONSUMED_TOPICS), topics - set(CONSUMED_TOPICS)
    assert DEFINITION.consumer_topics == CONSUMED_TOPICS


def test_registry_listens_exactly_to_catalog_events() -> None:
    from gv_service import Infrastructure

    infra = Infrastructure(settings=DEFINITION.settings_cls(), tables=DEFINITION.tables)
    assert DEFINITION.build_event_handlers is not None
    registry = DEFINITION.build_event_handlers(infra)
    assert registry.event_types == load_catalog().source_events


@pytest.mark.parametrize(
    ("event_type", "variant"),
    [
        ("progression.ProgressionLevelChanged", None),
        ("progression.BadgeUnlocked", None),
        ("progression.QuestCompleted", None),
        ("media_pipeline.AssetModerated", None),
        ("progression.FindUnlocked", None),
        ("progression.EventCompleted", None),
    ],
)
def test_contract_example_reaches_a_notification(event_type: str, variant: str | None) -> None:
    catalog = load_catalog()
    envelope = load_example(event_type, variant=variant)
    payload: dict[str, Any] = dict(envelope.payload)
    assert validation_errors(event_type, payload) == []
    matched = [t for t in catalog.for_event(event_type) if matches_filter(t.filter, payload)]
    assert len(matched) == 1
    result = map_event(matched[0], payload, locale="ru", default_locale=catalog.default_locale)
    assert result.title
    assert str(result.user_id) in {
        payload.get("user_id"),
        payload.get("owner_user_id"),
    }


@pytest.mark.parametrize(
    ("reason", "type_key"), [("weekly_top", "weekly_top_reward"), ("season", "season_reward")]
)
def test_currency_credited_reasons_select_reward_types(reason: str, type_key: str) -> None:
    catalog = load_catalog()
    payload = dict(load_example("progression.CurrencyCredited").payload)
    payload["reason"] = reason
    matched = [
        t
        for t in catalog.for_event("progression.CurrencyCredited")
        if matches_filter(t.filter, payload)
    ]
    assert [t.key for t in matched] == [type_key]
    assert map_event(matched[0], payload, locale="ru", default_locale="ru").dedup_key.endswith(
        str(payload["op_id"])
    )


def test_other_currency_reasons_produce_no_notification() -> None:
    catalog = load_catalog()
    payload = dict(load_example("progression.CurrencyCredited").payload)  # reason = duel
    assert not any(
        matches_filter(t.filter, payload) for t in catalog.for_event("progression.CurrencyCredited")
    )


def test_event_run_started_example_selects_audience_type() -> None:
    catalog = load_catalog()
    payload = dict(load_example("progression.EventRunStarted").payload)
    assert validation_errors("progression.EventRunStarted", payload) == []
    [notification_type] = catalog.for_event("progression.EventRunStarted")
    assert notification_type.audience == "known_users"
    user = uuid7()
    result = map_event(notification_type, payload, locale="ru", default_locale="ru", user_id=user)
    assert result.user_id == user
    assert result.dedup_key == f"event_started:{payload['run_id']}"
    assert result.data == {"event_key": payload["event_key"]}
