"""Аудит append-only: запрет TRUNCATE.

Строчный триггер из ``0001`` не срабатывает на ``TRUNCATE`` — им журнал можно было очистить
одной командой. Добавляем триггер уровня оператора на ту же функцию (она только бросает
исключение). Таблицу не переписывает и долгих блокировок не берёт: ``CREATE TRIGGER``
— короткая блокировка ``SHARE ROW EXCLUSIVE``.
"""

# ruff: noqa: RUF012 — атрибуты миграции Django (формат makemigrations)

from django.db import migrations

FORBID_TRUNCATE_SQL = """
CREATE TRIGGER audit_auditentry_no_truncate
    BEFORE TRUNCATE ON audit_auditentry
    FOR EACH STATEMENT EXECUTE FUNCTION audit_auditentry_append_only();
"""
ALLOW_TRUNCATE_SQL = "DROP TRIGGER IF EXISTS audit_auditentry_no_truncate ON audit_auditentry;"


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]

    operations = [migrations.RunSQL(FORBID_TRUNCATE_SQL, ALLOW_TRUNCATE_SQL)]
