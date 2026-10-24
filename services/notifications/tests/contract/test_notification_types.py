"""Контракт типов уведомлений (ADR 0047): схема валидна, пример согласован с событиями."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from gv_testkit.contracts import load_event_schema

pytestmark = pytest.mark.contract

CONTRACTS = Path(__file__).resolve().parents[4] / "contracts" / "notifications"
PLACEHOLDER = re.compile(r"\{([a-z_.]+)\}")


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((CONTRACTS / name).read_text(encoding="utf-8"))
    return data


def _validator() -> Draft202012Validator:
    schema = _load("notification_types.v1.json")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _example() -> dict[str, Any]:
    return _load("examples/notification_types.v1.json")


def _payload_field(path: str) -> str:
    assert path.startswith("payload."), path
    return path.removeprefix("payload.").split(".")[0]


def test_example_is_valid() -> None:
    assert list(_validator().iter_errors(_example())) == []


def test_example_types_are_unique_and_have_default_locale_template() -> None:
    config = _example()
    keys = [t["key"] for t in config["types"]]
    assert len(keys) == len(set(keys))
    for item in config["types"]:
        assert config["default_locale"] in item["templates"], item["key"]


def test_every_source_event_exists_and_its_fields_are_in_the_schema() -> None:
    """Получатель, подстановки, фильтры и ключ дедупликации ссылаются на поля события."""
    for item in _example()["types"]:
        schema = load_event_schema(item["source_event"])
        fields = set(schema["properties"])
        assert ("recipient" in item) != ("audience" in item), item["key"]
        if "recipient" in item:
            assert _payload_field(item["recipient"]) in fields, item["key"]
        for path in item.get("data", {}).values():
            assert _payload_field(path) in fields, (item["key"], path)
        for path in item["params"].values():
            assert _payload_field(path) in fields, (item["key"], path)
        for lookup in item.get("lookups", {}).values():
            assert _payload_field(lookup["path"]) in fields, (item["key"], lookup["path"])
        assert set(item.get("filter", {})) <= fields, item["key"]
        for ref in PLACEHOLDER.findall(item["dedup_key"]):
            assert _payload_field(ref) in fields, (item["key"], ref)


def test_template_placeholders_are_declared_params() -> None:
    for item in _example()["types"]:
        for locale, template in item["templates"].items():
            for text in (template["title"], template["body"]):
                declared = set(item["params"]) | set(item.get("lookups", {}))
                assert set(PLACEHOLDER.findall(text)) <= declared, (
                    item["key"],
                    locale,
                )


def test_dedup_key_never_uses_event_id() -> None:
    """Дедупликация - по предмету события (ADR 0047), повтор с новым event_id не двоит."""
    for item in _example()["types"]:
        assert "event_id" not in item["dedup_key"], item["key"]


def _no_templates(c: dict[str, Any]) -> None:
    c["types"][0]["templates"] = {}


def _bad_key(c: dict[str, Any]) -> None:
    c["types"][0]["key"] = "Level Up"


def _missing_channels(c: dict[str, Any]) -> None:
    del c["types"][0]["channels"]["push"]


def _unknown_field(c: dict[str, Any]) -> None:
    c["types"][0]["color"] = "red"


def _empty_types(c: dict[str, Any]) -> None:
    c["types"] = []


@pytest.mark.parametrize(
    "mutate", [_no_templates, _bad_key, _missing_channels, _unknown_field, _empty_types]
)
def test_invalid_config_is_rejected(mutate: Any) -> None:
    config = copy.deepcopy(_example())
    mutate(config)
    assert list(_validator().iter_errors(config)) != []
