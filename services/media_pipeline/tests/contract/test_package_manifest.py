"""Манифест пакета: схема ``contracts/packages`` ↔ разбор в сервисе (они не должны расходиться)."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from media_pipeline_service.domain.packages import (
    ROLE_KINDS,
    PackageRejectedError,
    parse_manifest,
)
from media_pipeline_service.domain.sources import FAMILY_BY_KIND, SourceKind

from gv_testkit.contracts import contracts_dir

pytestmark = pytest.mark.contract

SCHEMA_PATH = contracts_dir() / "packages" / "media_package_manifest.v1.json"
EXAMPLE_PATH = contracts_dir() / "packages" / "examples" / "media_package_manifest.v1.json"


def validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def example() -> dict[str, Any]:
    return json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))


def test_example_is_valid_and_parses() -> None:
    assert not list(validator().iter_errors(example()))
    manifest = parse_manifest(json.dumps(example()).encode(), max_files=100)
    assert [i.key for i in manifest.items] == ["track:fma-123", "film:tmdb-550"]
    assert manifest.file_count == 4


def test_roles_in_schema_match_service_roles() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    item = schema["properties"]["items"]["items"]
    assert set(item["properties"]["files"]["items"]["properties"]["role"]["enum"]) == set(
        ROLE_KINDS
    )
    # роль манифеста = вид файлового исходника (text приходит из catalog)
    assert set(ROLE_KINDS.values()) == set(FAMILY_BY_KIND) - {SourceKind.TEXT}


def _mutations() -> list[tuple[str, Any]]:
    def mutate(change: Any) -> dict[str, Any]:
        doc = copy.deepcopy(example())
        change(doc)
        return doc

    return [
        ("version2", mutate(lambda d: d.update(version=2))),
        ("no_items", mutate(lambda d: d.update(items=[]))),
        ("extra_top", mutate(lambda d: d.update(extra=1))),
        ("bad_key", mutate(lambda d: d["items"][0].update(key="Bad Key"))),
        ("bad_kind", mutate(lambda d: d["items"][0].update(kind="series"))),
        ("bad_origin", mutate(lambda d: d["items"][0].update(origin="catalog"))),
        ("bad_uuid", mutate(lambda d: d["items"][0].update(media_item_id="nope"))),
        ("no_files", mutate(lambda d: d["items"][0].update(files=[]))),
        ("bad_role", mutate(lambda d: d["items"][0]["files"][0].update(role="cover"))),
        ("position0", mutate(lambda d: d["items"][1]["files"][1].update(position=0))),
        ("extra_file", mutate(lambda d: d["items"][0]["files"][0].update(extra=1))),
        ("long_license", mutate(lambda d: d["items"][0].update(license_note="x" * 501))),
        ("extra_item", mutate(lambda d: d["items"][0].update(license="CC0"))),
    ]


@pytest.mark.parametrize(("name", "doc"), _mutations(), ids=[n for n, _ in _mutations()])
def test_schema_and_parser_agree_on_invalid_documents(name: str, doc: dict[str, Any]) -> None:
    assert list(validator().iter_errors(doc)), f"схема приняла {name}"
    with pytest.raises(PackageRejectedError):
        parse_manifest(json.dumps(doc).encode(), max_files=100)


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"license_note": ""},
        {"license_note": None},
        {"source_url": None, "license_note": ""},
    ],
)
def test_empty_or_missing_license_and_source_are_valid(extra: dict[str, Any]) -> None:
    doc = copy.deepcopy(example())
    for key in ("license_note", "source_url"):
        doc["items"][1].pop(key, None)
    doc["items"][1].update(extra)
    assert not list(validator().iter_errors(doc))
    (_, film) = parse_manifest(json.dumps(doc).encode(), max_files=100).items
    assert film.license_note is None and film.source_url is None
