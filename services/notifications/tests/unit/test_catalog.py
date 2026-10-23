"""Каталог типов уведомлений: разбор и проверка конфига при старте (ADR 0047)."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from notifications_service.domain.catalog import TypeCatalog
from notifications_service.domain.errors import ConfigError
from notifications_service.infrastructure.config import (
    load_catalog,
    read_document,
    validate_document,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def document() -> dict[str, Any]:
    return copy.deepcopy(read_document())


def test_builtin_config_loads_all_types_of_stage_5b() -> None:
    catalog = load_catalog()
    assert {t.key for t in catalog.types} == {
        "level_up",
        "badge_unlocked",
        "quest_completed",
        "weekly_top_reward",
        "season_reward",
        "avatar_approved",
        "avatar_rejected",
    }
    assert catalog.default_locale == "ru"


def test_types_are_found_by_key_and_source_event() -> None:
    catalog = load_catalog()
    assert catalog.by_key("level_up") is not None
    assert catalog.by_key("nope") is None
    credited = catalog.for_event("progression.CurrencyCredited")
    assert {t.key for t in credited} == {"weekly_top_reward", "season_reward"}
    assert catalog.for_event("game.GameFinished") == ()
    assert "media_pipeline.AssetModerated" in catalog.source_events


def test_topics_follow_source_events() -> None:
    catalog = load_catalog()
    assert catalog.source_events == frozenset(
        {
            "progression.ProgressionLevelChanged",
            "progression.BadgeUnlocked",
            "progression.QuestCompleted",
            "progression.CurrencyCredited",
            "media_pipeline.AssetModerated",
        }
    )


def test_missing_default_locale_template_is_config_error(document: dict[str, Any]) -> None:
    document["types"][0]["templates"] = {"en": {"title": "Level", "body": "Level {level}"}}
    with pytest.raises(ConfigError, match="level_up"):
        TypeCatalog.parse(document)


def test_placeholder_without_param_is_config_error(document: dict[str, Any]) -> None:
    document["types"][0]["templates"]["ru"]["body"] = "Уровень {unknown}"
    with pytest.raises(ConfigError, match="unknown"):
        TypeCatalog.parse(document)


def test_duplicate_keys_are_config_error(document: dict[str, Any]) -> None:
    document["types"].append(copy.deepcopy(document["types"][0]))
    with pytest.raises(ConfigError, match="level_up"):
        TypeCatalog.parse(document)


def test_dedup_key_from_event_id_is_config_error(document: dict[str, Any]) -> None:
    document["types"][0]["dedup_key"] = "level:{event_id}"
    with pytest.raises(ConfigError, match="dedup_key"):
        TypeCatalog.parse(document)


def test_dedup_key_must_reference_payload_fields(document: dict[str, Any]) -> None:
    document["types"][0]["dedup_key"] = "level:{user}"
    with pytest.raises(ConfigError, match="dedup_key"):
        TypeCatalog.parse(document)


def test_dedup_key_without_placeholders_is_config_error(document: dict[str, Any]) -> None:
    """Постоянный ключ дал бы одно уведомление на тип навсегда."""
    document["types"][0]["dedup_key"] = "level_up"
    with pytest.raises(ConfigError, match="dedup_key"):
        TypeCatalog.parse(document)


def test_paths_must_start_with_payload(document: dict[str, Any]) -> None:
    document["types"][0]["recipient"] = "event_id"
    with pytest.raises(ConfigError, match="recipient"):
        TypeCatalog.parse(document)


def test_schema_rejects_unknown_field(document: dict[str, Any]) -> None:
    document["types"][0]["color"] = "red"
    with pytest.raises(ConfigError):
        validate_document(document)


def test_schema_rejects_empty_types(document: dict[str, Any]) -> None:
    document["types"] = []
    with pytest.raises(ConfigError):
        validate_document(document)


def test_load_catalog_from_custom_path(tmp_path: Any) -> None:
    path = tmp_path / "types.yaml"
    path.write_text(
        "default_locale: ru\n"
        "types:\n"
        "  - key: t\n"
        "    source_event: x.Y\n"
        "    recipient: payload.u\n"
        "    dedup_key: 't:{payload.u}'\n"
        "    user_mutable: true\n"
        "    channels: {in_app: true, email: false, push: false}\n"
        "    params: {}\n"
        "    templates: {ru: {title: T, body: B}}\n",
        encoding="utf-8",
    )
    assert load_catalog(path).by_key("t") is not None


def test_yaml_is_not_a_mapping_is_config_error(tmp_path: Any) -> None:
    path = tmp_path / "types.yaml"
    path.write_text("- just\n- a list\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_catalog(path)


def _avatar_rejected(document: dict[str, Any]) -> dict[str, Any]:
    return next(t for t in document["types"] if t["key"] == "avatar_rejected")


def test_lookup_name_cannot_also_be_a_param(document: dict[str, Any]) -> None:
    """Имя подстановки либо из params, либо из lookups: двусмысленность запрещена."""
    _avatar_rejected(document)["params"] = {"reason": "payload.reason_code"}
    with pytest.raises(ConfigError, match="reason"):
        TypeCatalog.parse(document)


def test_lookup_without_default_locale_fallback_is_config_error(
    document: dict[str, Any],
) -> None:
    _avatar_rejected(document)["lookups"]["reason"]["fallback"] = {"en": "x"}
    with pytest.raises(ConfigError, match="fallback"):
        TypeCatalog.parse(document)


def test_lookup_code_without_default_locale_text_is_config_error(
    document: dict[str, Any],
) -> None:
    _avatar_rejected(document)["lookups"]["reason"]["texts"]["other"] = {"en": "x"}
    with pytest.raises(ConfigError, match="other"):
        TypeCatalog.parse(document)


def test_lookup_path_outside_payload_is_rejected_by_schema(document: dict[str, Any]) -> None:
    _avatar_rejected(document)["lookups"]["reason"]["path"] = "event_id"
    with pytest.raises(ConfigError):
        validate_document(document)


def test_lookup_code_must_be_snake_case(document: dict[str, Any]) -> None:
    _avatar_rejected(document)["lookups"]["reason"]["texts"]["Bad Code"] = {"ru": "x"}
    with pytest.raises(ConfigError):
        validate_document(document)


def test_placeholder_not_in_params_or_lookups_is_still_error(document: dict[str, Any]) -> None:
    _avatar_rejected(document)["templates"]["ru"]["body"] = "Аватарка {why}."
    with pytest.raises(ConfigError, match="why"):
        TypeCatalog.parse(document)
