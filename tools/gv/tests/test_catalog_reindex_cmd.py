"""Команда ``gv dev catalog-reindex``: аргументы docker compose."""

from __future__ import annotations

import pytest
import typer

from gv_cli.main import catalog_reindex_args

pytestmark = pytest.mark.unit

BASE = ["exec", "-T", "catalog", "service-entry", "reindex"]


def test_default_has_no_batch_so_settings_apply() -> None:
    assert catalog_reindex_args(no_switch=False, rollback=False, batch=None) == BASE


def test_flags_and_batch_are_passed() -> None:
    assert catalog_reindex_args(no_switch=True, rollback=False, batch=100) == [
        *BASE, "--batch", "100", "--no-switch",
    ]  # fmt: skip
    assert catalog_reindex_args(no_switch=False, rollback=False, batch=None, recreate=True)[-1] == (
        "--recreate"
    )
    assert catalog_reindex_args(no_switch=False, rollback=True, batch=None)[-1] == "--rollback"


@pytest.mark.parametrize("kw", [{"no_switch": True}, {"recreate": True}])
def test_rollback_conflicts(kw: dict[str, bool]) -> None:
    with pytest.raises(typer.BadParameter):
        catalog_reindex_args(**{"no_switch": False, "rollback": True, "batch": None, **kw})
