"""Поле ``data`` типа уведомления: разбор каталога и заполнение из события (5c, room_invite)."""

from __future__ import annotations

import copy
from dataclasses import replace
from typing import Any

import pytest
from notifications_service.domain.catalog import TypeCatalog
from notifications_service.domain.errors import ConfigError, MappingError
from notifications_service.domain.mapping import map_event
from notifications_service.infrastructure.config import load_catalog, read_document

from gv_testkit import load_example

pytestmark = pytest.mark.unit


@pytest.fixture(scope="module")
def catalog() -> TypeCatalog:
    return load_catalog()


def payload_of(name: str, producer: str) -> dict[str, Any]:
    return dict(load_example(f"{producer}.{name}").payload)


def test_room_invite_data_is_parsed_from_config(catalog: TypeCatalog) -> None:
    invite = catalog.by_key("room_invite")
    assert invite is not None
    assert dict(invite.data) == {"room_code": "payload.room_code"}
    level_up = catalog.by_key("level_up")
    assert level_up is not None
    assert dict(level_up.data) == {}


def test_data_path_must_start_with_payload() -> None:
    document = copy.deepcopy(read_document())
    document["types"][0]["data"] = {"x": "event_id"}
    with pytest.raises(ConfigError):
        TypeCatalog.parse(document)


def test_room_invite_data_is_filled_from_payload(catalog: TypeCatalog) -> None:
    invite = catalog.by_key("room_invite")
    assert invite is not None
    candidate = map_event(
        invite, payload_of("RoomInviteSent", "social"), locale="ru", default_locale="ru"
    )
    assert candidate.data == {"room_code": "K7M2QX"}
    assert "K7M2QX" not in candidate.body  # код - в data, не в тексте


def test_type_without_data_gives_empty_data(catalog: TypeCatalog) -> None:
    level_up = catalog.by_key("level_up")
    assert level_up is not None
    candidate = map_event(
        level_up,
        payload_of("ProgressionLevelChanged", "progression"),
        locale="ru",
        default_locale="ru",
    )
    assert candidate.data == {}


def test_data_values_are_converted_to_strings(catalog: TypeCatalog) -> None:
    base = catalog.by_key("level_up")
    assert base is not None
    typed = replace(base, data={"lvl": "payload.new_level"})
    candidate = map_event(
        typed,
        payload_of("ProgressionLevelChanged", "progression"),
        locale="ru",
        default_locale="ru",
    )
    assert isinstance(candidate.data["lvl"], str)
    assert candidate.data["lvl"].isdigit()


def test_data_path_missing_in_event_is_mapping_error(catalog: TypeCatalog) -> None:
    base = catalog.by_key("level_up")
    assert base is not None
    typed = replace(base, data={"x": "payload.no_such"})
    with pytest.raises(MappingError):
        map_event(
            typed,
            payload_of("ProgressionLevelChanged", "progression"),
            locale="ru",
            default_locale="ru",
        )
