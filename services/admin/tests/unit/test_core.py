"""Чистые части ядра: allowlist IP, разбор DATABASE_URL, роутер реплик, формы."""

from __future__ import annotations

from typing import Any

import pytest
from admin_service.apps.catalog.forms import AliasesForm, ImportForm
from admin_service.core.middleware import ip_allowed
from admin_service.core.replica import ReplicaRouter, ServiceModelAdmin
from admin_service.django_settings import database_from_url
from django.core.exceptions import ImproperlyConfigured

pytestmark = pytest.mark.unit

NETS = ("10.0.0.0/8", "::1/128")


@pytest.mark.parametrize(
    ("addr", "ok"),
    [
        ("10.1.2.3", True),
        ("::1", True),
        ("11.0.0.1", False),
        ("", False),
        (None, False),
        ("x", False),
    ],
)
def test_ip_allowed(addr: str | None, ok: bool) -> None:
    assert ip_allowed(addr, NETS) is ok


def test_database_url_drops_sqlalchemy_driver() -> None:
    db = database_from_url("postgresql+asyncpg://admin:p%40ss@postgres:5432/admin")
    assert db["ENGINE"] == "django.db.backends.postgresql"
    assert (db["NAME"], db["USER"], db["PASSWORD"], db["HOST"], db["PORT"]) == (
        "admin",
        "admin",
        "p@ss",
        "postgres",
        "5432",
    )
    assert database_from_url(None)["ENGINE"] == "django.db.backends.dummy"
    with pytest.raises(ImproperlyConfigured):
        database_from_url("mysql://x@y/z")


class _Meta:
    app_label = "catalog_replica"


class _Model:
    _meta = _Meta()


def test_replica_router_reads_replica_and_forbids_writes() -> None:
    router = ReplicaRouter({"catalog_replica": "catalog_ro"})
    model: Any = _Model
    assert router.db_for_read(model) == "catalog_ro"
    with pytest.raises(PermissionError):
        router.db_for_write(model)
    assert router.allow_migrate("catalog_ro", "core") is False
    assert router.allow_migrate("default", "catalog_replica") is False
    assert router.allow_migrate("default", "core") is None


def test_service_model_admin_is_read_only() -> None:
    admin: Any = ServiceModelAdmin.__new__(ServiceModelAdmin)
    assert not admin.has_add_permission(None)
    assert not admin.has_change_permission(None)
    assert not admin.has_delete_permission(None)
    with pytest.raises(PermissionError):
        admin.save_model(None, None, None, change=True)
    with pytest.raises(PermissionError):
        admin.delete_model(None, None)


def test_new_form_has_idempotency_key() -> None:
    first, second = ImportForm(), ImportForm()
    assert first.initial["idempotency_key"] != second.initial["idempotency_key"]


def test_aliases_limits() -> None:
    form = AliasesForm(
        {
            "aliases": "\n".join(f"a{i}" for i in range(101)),
            "reason": "abc",
            "idempotency_key": "0192a3b4-c5d6-7e8f-9a0b-000000000001",
        }
    )
    assert not form.is_valid()
    assert "aliases" in form.errors


@pytest.mark.parametrize(("length", "ok"), [(100, True), (101, False)])
def test_import_file_id_limit_matches_catalog_contract(length: int, ok: bool) -> None:
    form = ImportForm(
        {
            "source": "file",
            "file_id": "f" * length,
            "reason": "сиды",
            "idempotency_key": "0192a3b4-c5d6-7e8f-9a0b-000000000001",
        }
    )
    assert form.is_valid() is ok
