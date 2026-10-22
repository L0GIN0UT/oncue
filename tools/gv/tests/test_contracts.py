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
    def test_object_to_one_of_with_compatible_branch_is_not_breaking(self) -> None:
        """ADR 0032: session стал oneOf — старая ветка solo сохранена в $defs."""
        solo = _obj({"kind": {"type": "string"}, "rounds": {"type": "integer"}}, ["kind"])
        old = _obj({"session": solo})
        online = _obj({"kind": {"type": "string"}, "players": {"type": "integer"}}, ["kind"])
        new = {
            **_obj({"session": {"type": "object", "oneOf": [{"$ref": "#/$defs/S"}, online]}}),
            "$defs": {"S": solo},
        }
        assert contracts.schema_breaking("f", old, new) == []

    def test_object_to_one_of_without_compatible_branch_is_breaking(self) -> None:
        old = _obj({"session": _obj({"rounds": {"type": "integer"}})})
        new = _obj(
            {
                "session": {
                    "type": "object",
                    "oneOf": [
                        _obj({"rounds": {"type": "string"}}),
                        _obj({"players": {"type": "integer"}}),
                    ],
                }
            }
        )
        problems = contracts.schema_breaking("f", old, new)
        assert [p.detail for p in problems] == [
            "$.session: ни одна ветка oneOf/anyOf не совместима со старой схемой"
        ]

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


class TestHandWrittenContracts:
    """Схемы событий, параметров загадок и конфигов режимов пишутся руками (contract-first)."""

    @staticmethod
    def _schemas() -> list[Path]:
        root = ROOT / "contracts"
        return sorted(
            p
            for p in [
                *root.glob("events/*/*.json"),
                *root.glob("clues/*.params.v*.json"),
                *root.glob("modes/**/*.json"),
            ]
            if "examples" not in p.parts
        )

    def test_schemas_are_valid_json_schema(self) -> None:
        from jsonschema import Draft202012Validator

        schemas = self._schemas()
        assert schemas, "нет ни одной схемы"
        for path in schemas:
            Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))

    def test_every_event_has_valid_example(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from gv_testkit.contracts import assert_event_matches_schema, load_example

        monkeypatch.setenv("GV_CONTRACTS_DIR", str(ROOT / "contracts"))
        events = [p for p in self._schemas() if p.parent.parent.name == "events"]
        assert events
        for path in events:
            service, (event, version) = path.parent.name, path.stem.split(".v")
            envelope = load_example(f"{service}.{event}", int(version))
            assert envelope.event_type == f"{service}.{event}"
            assert_event_matches_schema(envelope)

    def test_schema_ids_match_paths(self) -> None:
        for path in self._schemas():
            schema = json.loads(path.read_text(encoding="utf-8"))
            # modes/params/source.daily.v1.json → «source.daily»; события и загадки — до точки.
            is_mode = "modes" in path.parts
            name = path.stem.rsplit(".v", 1)[0] if is_mode else path.stem.split(".")[0]
            assert f":{name}:" in schema["$id"], path

    def test_mode_schemas_are_covered(self) -> None:
        names = {p.name for p in self._schemas() if "modes" in p.parts}
        assert {"mode_config.v1.json", "pool.v1.json", "source.daily.v1.json"} <= names

    def test_params_hash_golden_is_consistent(self) -> None:
        """Golden-векторы params_hash (D7): у каждого вектора есть вход, JSON и хэш."""
        import hashlib

        golden = json.loads((ROOT / "contracts/clues/params_hash.golden.json").read_text("utf-8"))
        assert golden["vectors"]
        for vector in golden["vectors"]:
            digest = hashlib.sha256(vector["canonical_json"].encode()).hexdigest()
            assert digest == vector["params_hash"], vector["name"]


def test_compare_with_base_covers_nested_mode_contracts(tmp_path: Path) -> None:
    """contracts/modes/** (в том числе params/) сверяется с базой, как события и загадки."""

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    params = tmp_path / "contracts" / "modes" / "params"
    params.mkdir(parents=True)
    schema = params / "scorer.by_level.v1.json"
    schema.write_text(contracts.dump(_obj({"points": {"type": "array"}})))
    git("init", "-q", "-b", "main")
    git("-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")

    schema.write_text(contracts.dump(_obj({"points": {"type": "array"}}, ["points"])))
    problems = contracts.compare_with_base(tmp_path, "main")
    assert [p.file for p in problems] == ["contracts/modes/params/scorer.by_level.v1.json"]


def test_compare_with_base_ignores_django_snapshot(tmp_path: Path) -> None:
    """Снимок OpenAPI Django-сервиса можно удалить: он не публикуется (render_all)."""
    from gv_cli.registry import Service

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    openapi = tmp_path / "contracts" / "openapi"
    openapi.mkdir(parents=True)
    (openapi / "admin.json").write_text(contracts.dump({"paths": {"/v1/meta": {"get": {}}}}))
    git("init", "-q", "-b", "main")
    git("-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
    (openapi / "admin.json").unlink()

    django = [s for s in load_services(ROOT) if s.is_django]
    assert django and all(isinstance(s, Service) for s in django)
    assert len(contracts.compare_with_base(tmp_path, "main")) == 1
    assert contracts.compare_with_base(tmp_path, "main", django) == []
