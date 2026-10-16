"""Конфиг полок и ссылки на полку (D11): встроенный конфиг, проверки, строгий ``shelf_ref``."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import yaml
from library_service.domain.errors import ShelfNotFoundError
from library_service.domain.shelves import (
    CATALOG_DOMAINS,
    ShelfCatalog,
    ShelfRef,
    ShelvesConfigError,
)
from library_service.infrastructure.shelves_config import load_shelf_catalog

pytestmark = pytest.mark.unit

BASE: dict[str, Any] = {
    "version": 1,
    "system": [
        {"key": "a", "title_key": "t.a", "domains": ["film"], "exclusive_with": ["b"]},
        {"key": "b", "title_key": "t.b", "domains": ["film", "music"]},
    ],
    "custom": {"domains": ["music", "film"]},
    "card": {"person_roles": ["director"], "persons_max": 2},
}


def test_builtin_config_matches_plan() -> None:
    catalog = load_shelf_catalog()
    assert [s.key for s in catalog.system] == ["watch_later", "watched", "favorite"]
    assert catalog.spec("watched").exclusive_with == ("watch_later",)
    assert catalog.spec("watch_later").exclusive_with == ("watched",)
    assert catalog.spec("favorite").domains == ("music", "film")
    assert catalog.custom_domains == ("music", "film")


def test_custom_config_file(tmp_path: Path) -> None:
    path = tmp_path / "shelves.yaml"
    path.write_text(yaml.safe_dump(BASE), encoding="utf-8")
    catalog = load_shelf_catalog(path)
    assert [s.key for s in catalog.system] == ["a", "b"]
    assert catalog.card.persons_max == 2


def _broken(mutate: Any) -> dict[str, Any]:
    data = copy.deepcopy(BASE)
    mutate(data)
    return data


@pytest.mark.parametrize(
    "data",
    [
        None,
        _broken(lambda d: d.update(version=2)),
        _broken(lambda d: d.update(system=[])),
        _broken(lambda d: d["system"].append(dict(d["system"][0]))),
        _broken(lambda d: d["system"][0].update(key="Bad-Key")),
        _broken(lambda d: d["system"][0].update(exclusive_with=["zzz"])),
        _broken(lambda d: d["system"][0].update(exclusive_with=["a"])),
        _broken(lambda d: d["system"][0].update(domains=["books"])),
        _broken(lambda d: d["system"][0].update(domains=[])),
        _broken(lambda d: d["system"][0].update(title_key=None)),
        _broken(lambda d: d["custom"].update(domains=["film", "film"])),
        _broken(lambda d: d["card"].update(persons_max=-1)),
    ],
)
def test_invalid_config_is_rejected(data: Any) -> None:
    with pytest.raises(ShelvesConfigError):
        ShelfCatalog.from_mapping(data, domains=CATALOG_DOMAINS)


def test_unknown_system_key_is_not_found() -> None:
    with pytest.raises(ShelfNotFoundError):
        load_shelf_catalog().spec("wishlist")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("system:watch_later", ShelfRef(key="watch_later")),
        (
            "0192a3b4-c5d6-7e8f-9a0b-000000000d01",
            ShelfRef(shelf_id=UUID("0192a3b4-c5d6-7e8f-9a0b-000000000d01")),
        ),
    ],
)
def test_shelf_ref_roundtrip(raw: str, expected: ShelfRef) -> None:
    ref = ShelfRef.parse(raw)
    assert ref == expected
    assert str(ref) == raw


@pytest.mark.parametrize(
    "raw",
    [
        "system:",
        "system:Watch",
        "system:9x",
        "watch_later",
        "123",
        "0192A3B4-C5D6-7E8F-9A0B-000000000D01",
        "0192a3b4c5d67e8f9a0b000000000d01",
        "{0192a3b4-c5d6-7e8f-9a0b-000000000d01}",
        "system:watched ",
    ],
)
def test_shelf_ref_is_strict(raw: str) -> None:
    with pytest.raises(ShelfNotFoundError):
        ShelfRef.parse(raw)
