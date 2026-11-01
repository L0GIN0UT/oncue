"""Команда ``gv dev catalog-reindex``: аргументы docker compose."""

from __future__ import annotations

import pytest
import typer

from gv_cli.main import catalog_reindex_args

pytestmark = pytest.mark.unit


def test_default_runs_service_entry_reindex_in_catalog() -> None:
    assert catalog_reindex_args(no_switch=False, rollback=False, batch=500) == [
        "exec", "-T", "catalog", "service-entry", "reindex", "--batch", "500",
    ]  # fmt: skip


def test_flags_are_passed() -> None:
    assert catalog_reindex_args(no_switch=True, rollback=False, batch=100)[-1] == "--no-switch"
    assert catalog_reindex_args(no_switch=False, rollback=True, batch=100)[-1] == "--rollback"


def test_no_switch_and_rollback_conflict() -> None:
    with pytest.raises(typer.BadParameter):
        catalog_reindex_args(no_switch=True, rollback=True, batch=1)
