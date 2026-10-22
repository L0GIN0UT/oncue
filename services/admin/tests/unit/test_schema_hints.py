"""Подсказки редактора по JSON Schema: ветки session (oneOf + $ref) и заготовка нового конфига.

Две формы схемы: контракт ``contracts/modes/mode_config.v1.json`` (``oneOf`` с ``$defs`` без
``title``, ``kind`` — ``enum``) и сводная схема game (pydantic: ``discriminator.mapping``,
``title``, описание полей — в «Attributes» докстринга, опциональные — ``anyOf [X, null]``).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from admin_service.apps.game_config import config_text, schema_hints

from gv_testkit.contracts import contracts_dir

pytestmark = pytest.mark.unit


def _contract() -> dict[str, Any]:
    path = contracts_dir() / "modes" / "mode_config.v1.json"
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


# Форма ответа game GET /admin/v1/mode-configs/schema (сокращённо, как выдаёт pydantic).
PYDANTIC: dict[str, Any] = {
    "mode_config": {
        "type": "object",
        "required": ["key", "domain", "session"],
        "properties": {
            "key": {"type": "string", "pattern": "^[a-z.]+$"},
            "domain": {"enum": ["music", "film"], "type": "string"},
            "session": {
                "discriminator": {
                    "propertyName": "kind",
                    "mapping": {
                        "solo": "#/$defs/SoloSessionSpec",
                        "duel": "#/$defs/OnlineSessionSpec",
                        "party": "#/$defs/OnlineSessionSpec",
                    },
                },
                "oneOf": [
                    {"$ref": "#/$defs/SoloSessionSpec"},
                    {"$ref": "#/$defs/OnlineSessionSpec"},
                ],
            },
        },
        "$defs": {
            "SoloSessionSpec": {
                "title": "SoloSessionSpec",
                "type": "object",
                "required": ["kind", "rounds"],
                "properties": {
                    "kind": {"const": "solo", "type": "string"},
                    "rounds": {"type": "integer", "minimum": 1, "maximum": 200},
                },
            },
            "OnlineSessionSpec": {
                "title": "OnlineSessionSpec",
                "description": "Правила онлайн-сессии.\n\nAttributes:\n"
                "    kind: Вид комнаты.\n    players: Минимум и максимум\n"
                "        игроков.\n    ranked: Рейтинговый матч.\n",
                "type": "object",
                "required": ["kind", "players", "level_windows_s", "contest"],
                "properties": {
                    "kind": {"enum": ["duel", "party"], "type": "string"},
                    "players": {"$ref": "#/$defs/PlayersSpec"},
                    "level_windows_s": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 3, "maximum": 300},
                        "minItems": 1,
                        "maxItems": 20,
                    },
                    "contest": {"$ref": "#/$defs/PluginSpec"},
                    "ranked": {"type": "boolean", "default": False},
                    "ladder": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
                },
            },
            "PlayersSpec": {
                "type": "object",
                "required": ["min", "max"],
                "properties": {
                    "min": {"type": "integer", "minimum": 2, "maximum": 6},
                    "max": {"type": "integer", "minimum": 2, "maximum": 6},
                },
            },
            "PluginSpec": {
                "type": "object",
                "required": ["type", "params"],
                "properties": {"type": {"type": "string"}, "params": {"type": "object"}},
            },
        },
    },
    "plugins": {"contest": {"race": {}, "parallel": {}}, "scoring": {"by_level": {}}},
}


def _fields(variant: schema_hints.SessionVariant) -> dict[str, schema_hints.FieldHint]:
    return {f.name: f for f in variant.fields}


def test_contract_session_one_of_resolves_both_branches_via_ref() -> None:
    solo, online = schema_hints.session_variants(_contract())
    assert (solo.name, solo.kinds, solo.online) == ("SoloSession", ("solo",), False)
    assert (online.name, online.online) == ("OnlineSession", True)
    assert set(online.kinds) == {"duel", "party", "lobby"}
    fields = _fields(online)
    assert "allow_guest" not in fields  # ADR 0036: онлайн только с аккаунтом
    assert fields["players"].type in {"{max, min}", "{min, max}"}  # $ref не нужен: инлайн-объект
    assert fields["players"].required
    assert fields["lobby"].type == "{overridable, visibility} | null"  # anyOf [$ref, null]
    assert not fields["lobby"].required
    assert fields["level_windows_s"].constraints == "1–20 элементов, каждый 3..300"
    assert fields["ranked"].constraints == "по умолчанию false"


def test_pydantic_schema_uses_titles_attributes_and_discriminator() -> None:
    solo, online = schema_hints.session_variants(PYDANTIC)
    assert solo.kinds == ("solo",)  # const
    assert online.name == "OnlineSessionSpec"
    assert online.description == "Правила онлайн-сессии."
    fields = _fields(online)
    assert fields["players"].description == "Минимум и максимум игроков."
    assert fields["players"].type == "{min, max}"
    assert fields["contest"].type == "{type, params}"
    assert fields["ladder"].type == "string | null"
    assert schema_hints.plugin_types(PYDANTIC, "contest") == ["parallel", "race"]
    assert schema_hints.plugin_types(_contract(), "contest") == []


def test_variant_for_picks_branch_by_kind() -> None:
    variants = schema_hints.session_variants(_contract())
    online = schema_hints.variant_for(variants, "party")
    assert online is not None
    assert online.online
    assert schema_hints.variant_for(variants, "nope") is None
    assert schema_hints.variant_for(variants, None) is None


@pytest.mark.parametrize("schema", [None, [], {}, {"mode_config": {"properties": "x"}}])
def test_unexpected_schema_gives_no_hints(schema: Any) -> None:
    assert schema_hints.session_variants(schema) == []
    assert schema_hints.new_config(schema, key="music.a.b", domain="music", online=True) is None


def test_cyclic_ref_does_not_hang() -> None:
    schema = {
        "properties": {"session": {"$ref": "#/$defs/A"}},
        "$defs": {"A": {"$ref": "#/$defs/A"}},
    }
    assert schema_hints.session_variants(schema) == []


@pytest.mark.parametrize("schema", [PYDANTIC, None], ids=["pydantic", "contract"])
def test_new_online_config_is_skeleton_of_online_branch(schema: Any) -> None:
    schema = schema or _contract()
    body = schema_hints.new_config(schema, key="film.duel.test", domain="film", online=True)
    assert body is not None
    assert (body["key"], body["domain"]) == ("film.duel.test", "film")
    session = body["session"]
    assert session["kind"] == "duel"
    assert session["players"] == {"min": 2, "max": 2}  # минимумы из схемы
    assert session["level_windows_s"] == [3]
    assert session["contest"] == {"type": "", "params": {}}
    assert "allow_guest" not in session
    assert "ranked" not in session  # необязательные поля не добавляются


def test_new_solo_config_and_online_template_dump_in_logical_order() -> None:
    solo = schema_hints.new_config(_contract(), key="music.a.b", domain="music", online=False)
    assert solo is not None
    assert solo["session"]["kind"] == "solo"
    online = schema_hints.new_config(_contract(), key="music.a.b", domain="music", online=True)
    text = config_text.dump(online, "yaml")
    session = config_text.parse(text, "yaml")["session"]
    assert list(session)[:4] == ["kind", "players", "rounds", "countdown_s"]
    assert list(session["contest"]) == ["type", "params"]
