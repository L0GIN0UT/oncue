"""Тесты проверки контрактов: актуальность снимка и правила совместимости."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from gv_cli import contracts
from gv_cli.registry import load_services

ROOT = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.unit


def _obj(props: dict[str, object], required: list[str] | None = None) -> dict[str, object]:
    return {"type": "object", "properties": props, "required": required or []}


class TestSchemaCompatibility:
    def test_optional_field_is_compatible(self) -> None:
        old = _obj({"a": {"type": "integer"}}, ["a"])
        new = _obj({"a": {"type": "integer"}, "b": {"type": "string"}}, ["a"])
        assert contracts.schema_breaking("f", old, new) == []

    def test_removed_field_is_breaking(self) -> None:
        old = _obj({"a": {"type": "integer"}, "b": {"type": "string"}})
        new = _obj({"a": {"type": "integer"}})
        assert [str(b) for b in contracts.schema_breaking("f", old, new)] == [
            "f: $.b: свойство удалено"
        ]

    def test_newly_required_is_breaking(self) -> None:
        old = _obj({"a": {"type": "integer"}})
        new = _obj({"a": {"type": "integer"}}, ["a"])
        assert len(contracts.schema_breaking("f", old, new)) == 1

    def test_nested_type_change_is_breaking(self) -> None:
        old = _obj({"payload": _obj({"points": {"type": "integer"}})})
        new = _obj({"payload": _obj({"points": {"type": "string"}})})
        problems = contracts.schema_breaking("f", old, new)
        assert len(problems) == 1
        assert "$.payload.points" in problems[0].detail


class TestOpenApiCompatibility:
    def test_removed_operation_is_breaking(self) -> None:
        old = {"paths": {"/v1/a": {"get": {}, "post": {}}, "/v1/b": {"get": {}}}}
        new = {"paths": {"/v1/a": {"get": {}}, "/v1/c": {"get": {}}}}
        assert sorted(b.detail for b in contracts.openapi_breaking("f", old, new)) == [
            "удалена операция GET /v1/b",
            "удалена операция POST /v1/a",
        ]

    def test_added_operation_is_compatible(self) -> None:
        old = {"paths": {"/v1/a": {"get": {}}}}
        new = {"paths": {"/v1/a": {"get": {}, "parameters": []}, "/v1/b": {"post": {}}}}
        assert contracts.openapi_breaking("f", old, new) == []


def test_snapshot_is_up_to_date() -> None:
    """Закоммиченный снимок совпадает с кодом (иначе — `gv contracts export`)."""
    assert contracts.stale(ROOT, load_services(ROOT)) == []


def test_dump_is_deterministic() -> None:
    doc = {"b": 1, "a": {"y": 2, "x": "ё"}}
    assert contracts.dump(doc) == contracts.dump(json.loads(contracts.dump(doc)))
    assert "ё" in contracts.dump(doc)


def test_compare_with_base_detects_deleted_contract(tmp_path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    events = tmp_path / "contracts" / "events" / "game"
    events.mkdir(parents=True)
    schema = events / "Scored.v1.json"
    schema.write_text(contracts.dump(_obj({"points": {"type": "integer"}})))
    git("init", "-q", "-b", "main")
    git("-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")

    assert contracts.compare_with_base(tmp_path, "main") == []
    schema.write_text(contracts.dump(_obj({})))
    assert len(contracts.compare_with_base(tmp_path, "main")) == 1
    schema.unlink()
    assert "удалён" in contracts.compare_with_base(tmp_path, "main")[0].detail
