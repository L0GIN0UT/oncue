"""Маппинг события в уведомление: пути, фильтры, подстановка, ключ дедупликации."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from notifications_service.domain.catalog import TypeCatalog
from notifications_service.domain.errors import MappingError
from notifications_service.domain.mapping import (
    map_event,
    matches_filter,
    render,
    resolve_path,
)
from notifications_service.infrastructure.config import load_catalog

from gv_testkit import load_example

pytestmark = pytest.mark.unit

USER = "0192a3b4-c5d6-7e8f-9a0b-0000000b0001"


@pytest.fixture(scope="module")
def catalog() -> TypeCatalog:
    return load_catalog()


def payload_of(name: str, producer: str = "progression") -> dict[str, Any]:
    return dict(load_example(f"{producer}.{name}").payload)


def test_resolve_path_reads_nested_values() -> None:
    assert resolve_path({"a": {"b": 5}}, "payload.a.b") == 5


@pytest.mark.parametrize("path", ["payload.x", "payload.a.x", "payload.a.b.c"])
def test_resolve_path_missing_is_mapping_error(path: str) -> None:
    with pytest.raises(MappingError):
        resolve_path({"a": {"b": 5}}, path)


def test_resolve_path_outside_payload_is_mapping_error() -> None:
    with pytest.raises(MappingError):
        resolve_path({"a": 1}, "event_id")


def test_filter_is_equality_and_empty_filter_matches_everything(catalog: TypeCatalog) -> None:
    weekly = catalog.by_key("weekly_top_reward")
    assert weekly is not None
    assert matches_filter(weekly.filter, {"reason": "weekly_top"})
    assert not matches_filter(weekly.filter, {"reason": "season"})
    assert not matches_filter(weekly.filter, {})
    assert matches_filter({}, {"anything": 1})


def test_filter_does_not_confuse_bool_and_int() -> None:
    assert not matches_filter({"flag": True}, {"flag": 1})
    assert matches_filter({"flag": True}, {"flag": True})


def test_render_substitutes_params_and_leaves_foreign_braces() -> None:
    assert render("Уровень {level} из {{max}}", {"level": "5"}) == "Уровень 5 из {{max}}"


def test_render_does_not_expand_braces_inside_values() -> None:
    assert render("Бейдж {badge}", {"badge": "{level}", "level": "1"}) == "Бейдж {level}"


@given(st.text(max_size=300))
def test_render_never_fails_on_arbitrary_values(value: str) -> None:
    out = render("x {a} y", {"a": value})
    assert out.startswith("x ")
    assert out.endswith(" y")


def test_level_up_event_becomes_notification(catalog: TypeCatalog) -> None:
    level_up = catalog.by_key("level_up")
    assert level_up is not None
    payload = payload_of("ProgressionLevelChanged")
    result = map_event(level_up, payload, locale="ru", default_locale="ru")
    assert result.user_id == UUID(USER)
    assert result.type_key == "level_up"
    assert result.title == "Новый уровень"
    assert str(payload["new_level"]) in result.body
    assert result.dedup_key == f"level_up:{USER}:{payload['new_level']}"


def test_dedup_key_does_not_depend_on_event_id(catalog: TypeCatalog) -> None:
    """Ключ собирается из полей payload; других входов у маппинга нет."""
    level_up = catalog.by_key("level_up")
    assert level_up is not None
    payload = payload_of("ProgressionLevelChanged")
    first = map_event(level_up, payload, locale="ru", default_locale="ru")
    second = map_event(level_up, dict(payload), locale="ru", default_locale="ru")
    assert first.dedup_key == second.dedup_key


def test_unknown_locale_falls_back_to_default(catalog: TypeCatalog) -> None:
    level_up = catalog.by_key("level_up")
    assert level_up is not None
    result = map_event(
        level_up, payload_of("ProgressionLevelChanged"), locale="de", default_locale="ru"
    )
    assert result.title == "Новый уровень"


def test_avatar_recipient_is_owner_and_reason_comes_from_event(catalog: TypeCatalog) -> None:
    rejected = catalog.by_key("avatar_rejected")
    assert rejected is not None
    payload = {
        "asset_id": "0192a3b4-c5d6-7e8f-9a0b-0000000a0001",
        "owner_user_id": USER,
        "purpose": "avatar",
        "status": "rejected",
        "reason_code": "inappropriate",
        "version": 2,
        "decided_at": "2026-10-23T12:00:00Z",
    }
    assert matches_filter(rejected.filter, payload)
    result = map_event(rejected, payload, locale="ru", default_locale="ru")
    assert result.user_id == UUID(USER)
    assert result.body == "Ваша аватарка отклонена: нарушает правила сообщества."
    assert result.dedup_key == "avatar:0192a3b4-c5d6-7e8f-9a0b-0000000a0001:2"


def test_recipient_that_is_not_uuid_is_mapping_error(catalog: TypeCatalog) -> None:
    level_up = catalog.by_key("level_up")
    assert level_up is not None
    payload = {**payload_of("ProgressionLevelChanged"), "user_id": "not-a-uuid"}
    with pytest.raises(MappingError):
        map_event(level_up, payload, locale="ru", default_locale="ru")


def test_missing_field_is_mapping_error(catalog: TypeCatalog) -> None:
    level_up = catalog.by_key("level_up")
    assert level_up is not None
    payload = payload_of("ProgressionLevelChanged")
    del payload["new_level"]
    with pytest.raises(MappingError):
        map_event(level_up, payload, locale="ru", default_locale="ru")


def test_too_long_dedup_key_is_mapping_error(catalog: TypeCatalog) -> None:
    badge = catalog.by_key("badge_unlocked")
    assert badge is not None
    payload = {**payload_of("BadgeUnlocked"), "badge_key": "x" * 300}
    with pytest.raises(MappingError):
        map_event(badge, payload, locale="ru", default_locale="ru")


def test_long_param_value_is_truncated_in_text(catalog: TypeCatalog) -> None:
    badge = catalog.by_key("badge_unlocked")
    assert badge is not None
    payload = {**payload_of("BadgeUnlocked"), "badge_key": "b" * 150}
    result = map_event(badge, payload, locale="ru", default_locale="ru")
    assert len(result.body) <= 500


# Коды причин отклонения аватарки - общий словарь с media-pipeline (поток S3): менять только
# вместе с ним и с ``services/notifications/CLAUDE.md``.
AVATAR_REASON_CODES = {"inappropriate", "copyright", "low_quality", "personal_data", "other"}


def _rejected_payload(reason_code: object) -> dict[str, Any]:
    return {
        "asset_id": "0192a3b4-c5d6-7e8f-9a0b-0000000a0001",
        "owner_user_id": USER,
        "purpose": "avatar",
        "status": "rejected",
        "reason_code": reason_code,
        "version": 2,
        "decided_at": "2026-10-23T12:00:00Z",
    }


def test_avatar_rejected_lookup_covers_the_agreed_reason_codes(catalog: TypeCatalog) -> None:
    rejected = catalog.by_key("avatar_rejected")
    assert rejected is not None
    assert set(rejected.lookups["reason"].texts) == AVATAR_REASON_CODES


@pytest.mark.parametrize("code", sorted(AVATAR_REASON_CODES))
def test_every_known_reason_code_has_own_text(catalog: TypeCatalog, code: str) -> None:
    rejected = catalog.by_key("avatar_rejected")
    assert rejected is not None
    result = map_event(rejected, _rejected_payload(code), locale="ru", default_locale="ru")
    assert result.body.startswith("Ваша аватарка отклонена: ")
    assert "{" not in result.body


@pytest.mark.parametrize("code", ["blurry", "", None, "x" * 500])
def test_unknown_or_empty_reason_code_gives_fallback(catalog: TypeCatalog, code: object) -> None:
    rejected = catalog.by_key("avatar_rejected")
    assert rejected is not None
    result = map_event(rejected, _rejected_payload(code), locale="ru", default_locale="ru")
    assert result.body == "Ваша аватарка не прошла модерацию."


def test_lookup_falls_back_to_default_locale_for_missing_locale(catalog: TypeCatalog) -> None:
    rejected = catalog.by_key("avatar_rejected")
    assert rejected is not None
    known = map_event(rejected, _rejected_payload("copyright"), locale="de", default_locale="ru")
    unknown = map_event(rejected, _rejected_payload("zzz"), locale="de", default_locale="ru")
    assert known.body == "Ваша аватарка отклонена: нарушает чужие авторские права."
    assert unknown.body == "Ваша аватарка не прошла модерацию."


def test_lookup_uses_requested_locale_when_it_has_text() -> None:
    catalog = TypeCatalog.parse(
        {
            "default_locale": "ru",
            "types": [
                {
                    "key": "t",
                    "source_event": "a.B",
                    "recipient": "payload.u",
                    "channels": {"in_app": True, "email": False, "push": False},
                    "user_mutable": True,
                    "dedup_key": "t:{payload.u}",
                    "params": {},
                    "lookups": {
                        "why": {
                            "path": "payload.code",
                            "texts": {"a": {"ru": "эй", "en": "hey"}},
                            "fallback": {"ru": "ничего", "en": "nothing"},
                        }
                    },
                    "templates": {
                        "ru": {"title": "T", "body": "Тут {why}."},
                        "en": {"title": "T", "body": "Here {why}."},
                    },
                }
            ],
        }
    )
    t = catalog.types[0]
    payload = {"u": USER, "code": "a"}
    assert map_event(t, payload, locale="en", default_locale="ru").body == "Here hey."
    other = {**payload, "code": "q"}
    assert map_event(t, other, locale="en", default_locale="ru").body == "Here nothing."
