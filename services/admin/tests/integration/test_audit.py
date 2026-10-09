"""Аудит append-only: и через ORM, и SQL-запросом в обход ORM."""

from __future__ import annotations

import pytest
from admin_service.audit.models import AppendOnlyError, AuditEntry
from admin_service.audit.services import record
from admin_service.core.models import AdminUser
from django.db import DatabaseError, connection, transaction

from gv_core.ids import uuid7

pytestmark = [pytest.mark.integration, pytest.mark.django_db]


@pytest.fixture
def entry() -> AuditEntry:
    actor = AdminUser.objects.create(username="a", identity_user_id=uuid7())
    return record(
        actor=actor,
        action="catalog.block_item",
        service="catalog",
        target_type="media_item",
        target_id="x",
        reason="r",
        request_id="rid",
        idempotency_key="k",
    )


def test_orm_update_and_delete_are_forbidden(entry: AuditEntry) -> None:
    entry.reason = "переписано"
    with pytest.raises(AppendOnlyError):
        entry.save()
    with pytest.raises(AppendOnlyError):
        entry.delete()
    with pytest.raises(AppendOnlyError):
        AuditEntry.objects.filter(pk=entry.pk).update(reason="x")
    with pytest.raises(AppendOnlyError):
        AuditEntry.objects.all().delete()


@pytest.mark.parametrize(
    "sql",
    ["UPDATE audit_auditentry SET reason = 'x'", "DELETE FROM audit_auditentry"],
)
def test_sql_update_and_delete_are_blocked_by_trigger(entry: AuditEntry, sql: str) -> None:
    with (
        pytest.raises(DatabaseError, match="append-only"),
        transaction.atomic(),
        connection.cursor() as cur,
    ):
        cur.execute(sql)
    assert AuditEntry.objects.get(pk=entry.pk).reason == "r"


def test_truncate_is_blocked_by_trigger(entry: AuditEntry) -> None:
    with connection.cursor() as cur:
        # Триггер создан миграцией; в тестовой БД он выключен ради flush (см. conftest) —
        # включаем в транзакции теста, откат вернёт как было.
        cur.execute("SELECT count(*) FROM pg_trigger WHERE tgname = 'audit_auditentry_no_truncate'")
        assert cur.fetchone() == (1,)
        # Отложенные проверки FK от вставки записи мешают ALTER TABLE — выполнить их сейчас.
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cur.execute("ALTER TABLE audit_auditentry ENABLE TRIGGER audit_auditentry_no_truncate")
    with (
        pytest.raises(DatabaseError, match="append-only"),
        transaction.atomic(),
        connection.cursor() as cur,
    ):
        cur.execute("TRUNCATE audit_auditentry")
    assert AuditEntry.objects.filter(pk=entry.pk).exists()
