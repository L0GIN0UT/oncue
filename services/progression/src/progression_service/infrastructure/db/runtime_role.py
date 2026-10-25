"""Права рантайм-роли Postgres, идемпотентно при каждом ``migrate`` (ADR 0060).

Миграция ``0009`` выдаёт права один раз, но роль на существующем томе создаётся оператором уже
после неё. Поэтому job ``migrate`` повторяет выдачу при каждом запуске, если задана
``GV_PROGRESSION__RUNTIME_ROLE``, и падает, когда роли нет: молчаливый пропуск толкал бы к
``GRANT ALL`` и снова открывал журналы на изменение.

Журналы определяются не списком имён, а по триггеру запрета изменений
(``progression_ledger_forbid_mutation``): новый append-only журнал закрывается автоматически.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from gv_core.errors import DependencyUnavailableError

# Функция триггеров append-only (миграции 0002, 0004): таблицы с ней — журналы.
LEDGER_TRIGGER_FUNCTION = "progression_ledger_forbid_mutation"

_LEDGERS_SQL = text(
    """
    SELECT DISTINCT c.relname
    FROM pg_trigger t
    JOIN pg_proc p ON p.oid = t.tgfoid
    JOIN pg_class c ON c.oid = t.tgrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE p.proname = :fn AND n.nspname = 'public' AND NOT t.tgisinternal
    ORDER BY c.relname
    """
)


async def ensure_runtime_grants(database_url: str, role: str) -> list[str]:
    """Выдать рантайм-роли DML, отобрав изменение журналов; вернуть имена журналов.

    Выполняется владельцем схемы (DSN job ``migrate``).

    Raises:
        DependencyUnavailableError: роли ``role`` нет в кластере.
    """
    engine = create_async_engine(database_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}
            )
            if not exists:
                raise DependencyUnavailableError(
                    f"рантайм-роль Postgres {role!r} не создана (GV_PROGRESSION__RUNTIME_ROLE): "
                    "создайте её (ADR 0060) и повторите migrate"
                )
            quote = engine.dialect.identifier_preparer.quote
            r = quote(role)
            ledgers = [
                row[0] for row in await conn.execute(_LEDGERS_SQL, {"fn": LEDGER_TRIGGER_FUNCTION})
            ]
            statements = [
                f"GRANT USAGE ON SCHEMA public TO {r}",
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {r}",
                f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {r}",
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {r}",
                "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                f"GRANT USAGE, SELECT ON SEQUENCES TO {r}",
                *(
                    f"REVOKE UPDATE, DELETE, TRUNCATE ON {quote(table)} FROM {r}"
                    for table in ledgers
                ),
                f"REVOKE ALL ON alembic_version FROM {r}",
            ]
            for statement in statements:
                await conn.execute(text(statement))
            return ledgers
    finally:
        await engine.dispose()
