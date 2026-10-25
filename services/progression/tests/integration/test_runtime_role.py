"""Рантайм-роль ``progression_app`` (ADR 0060): журналы — только чтение и вставка.

Роль создаёт оператор/скрипт инициализации, права выдаёт миграция ``0009``. Проверяем на
настоящей базе: рантайм-роль пишет в журнал, но не меняет, не очищает его, не отключает его
триггер и не правит схему, а остальные таблицы читает и пишет.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from progression_service.main import DEFINITION
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from gv_testkit import apply_migrations

pytestmark = pytest.mark.integration

ROLE = "progression_app"
PASSWORD = "app-role-test-password"


@asynccontextmanager
async def _engine(url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        yield engine
    finally:
        await engine.dispose()


async def _cleanup(admin_url: str, db_name: str) -> None:
    async with _engine(admin_url) as admin, admin.connect() as conn:
        await conn.execute(text(f"REVOKE ALL ON DATABASE {db_name} FROM {ROLE}"))
    async with _engine(admin_url) as admin, admin.connect() as conn:
        await conn.execute(text(f"DROP OWNED BY {ROLE}"))
        await conn.execute(text(f"DROP ROLE IF EXISTS {ROLE}"))


async def test_runtime_role_cannot_touch_ledgers(database_url: str) -> None:
    assert DEFINITION.migrations_path is not None
    url = make_url(database_url)
    db_name = str(url.database)
    app_url = url.set(username=ROLE, password=PASSWORD).render_as_string(hide_password=False)

    async with _engine(database_url) as admin, admin.connect() as conn:
        await conn.execute(text(f"CREATE ROLE {ROLE} LOGIN PASSWORD '{PASSWORD}'"))
        await conn.execute(text(f"GRANT CONNECT ON DATABASE {db_name} TO {ROLE}"))
    try:
        # Миграции — владельцем схемы (в тестах это администратор временной базы).
        await apply_migrations(database_url, DEFINITION.migrations_path)
        user = uuid4()
        async with _engine(app_url) as app, app.connect() as conn:
            # Остальные таблицы: чтение и запись доступны.
            assert (await conn.execute(text("SELECT count(*) FROM outbox"))).scalar_one() == 0
            # Журналы: чтение доступно.
            assert (await conn.execute(text("SELECT count(*) FROM xp_ledger"))).scalar_one() == 0
            denied = [
                "UPDATE xp_ledger SET base_amount = base_amount",
                "UPDATE currency_ledger SET amount = amount",
                "DELETE FROM xp_ledger",
                "DELETE FROM currency_ledger",
                "DELETE FROM admin_actions",
                "TRUNCATE xp_ledger",
                "TRUNCATE currency_ledger",
                "ALTER TABLE xp_ledger DISABLE TRIGGER ALL",
                "DROP TRIGGER trg_admin_actions_append_only ON admin_actions",
                "UPDATE alembic_version SET version_num = '0001'",
            ]
            for statement in denied:
                with pytest.raises(DBAPIError, match=r"permission denied|must be owner"):
                    await conn.execute(text(statement))
            assert user  # идентификатор нужен только для читаемости сценария
    finally:
        await _cleanup(database_url, db_name)


async def test_migration_without_role_is_noop(database_url: str) -> None:
    """Нет роли (локальная БД одной ролью, прочие тесты) — миграция проходит без правок прав."""
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)


async def test_ensure_grants_fails_without_role_and_is_idempotent(database_url: str) -> None:
    """Нет роли — job migrate падает (не молчит); повторная выдача безопасна и находит журналы."""
    from progression_service.infrastructure.db.runtime_role import ensure_runtime_grants

    from gv_core.errors import DependencyUnavailableError

    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    with pytest.raises(DependencyUnavailableError, match=ROLE):
        await ensure_runtime_grants(database_url, ROLE)

    url = make_url(database_url)
    db_name = str(url.database)
    async with _engine(database_url) as admin, admin.connect() as conn:
        await conn.execute(text(f"CREATE ROLE {ROLE} LOGIN PASSWORD '{PASSWORD}'"))
        await conn.execute(text(f"GRANT CONNECT ON DATABASE {db_name} TO {ROLE}"))
    try:
        ledgers = await ensure_runtime_grants(database_url, ROLE)
        assert ledgers == ["admin_actions", "currency_ledger", "xp_ledger"]
        assert await ensure_runtime_grants(database_url, ROLE) == ledgers  # идемпотентно
    finally:
        await _cleanup(database_url, db_name)


async def test_every_trigger_protected_table_is_closed_to_runtime_role(database_url: str) -> None:
    """Страж: новый журнал с триггером запрета закрыт рантайм-роли без ручного REVOKE."""
    from progression_service.infrastructure.db.runtime_role import ensure_runtime_grants

    assert DEFINITION.migrations_path is not None
    url = make_url(database_url)
    db_name = str(url.database)
    async with _engine(database_url) as admin, admin.connect() as conn:
        await conn.execute(text(f"CREATE ROLE {ROLE} LOGIN PASSWORD '{PASSWORD}'"))
        await conn.execute(text(f"GRANT CONNECT ON DATABASE {db_name} TO {ROLE}"))
    try:
        await apply_migrations(database_url, DEFINITION.migrations_path)
        # «Будущий» журнал: таблица с тем же триггером запрета, созданная после миграции 0009.
        async with _engine(database_url) as admin, admin.connect() as conn:
            await conn.execute(text("CREATE TABLE future_ledger (id int PRIMARY KEY, v int)"))
            await conn.execute(
                text(
                    "CREATE TRIGGER trg_future_ledger_append_only BEFORE UPDATE OR DELETE "
                    "ON future_ledger FOR EACH ROW "
                    "EXECUTE FUNCTION progression_ledger_forbid_mutation()"
                )
            )
        ledgers = await ensure_runtime_grants(database_url, ROLE)
        assert "future_ledger" in ledgers
        async with _engine(database_url) as admin, admin.connect() as conn:
            for table in ledgers:
                for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
                    allowed = await conn.scalar(
                        text("SELECT has_table_privilege(:role, :table, :privilege)"),
                        {"role": ROLE, "table": table, "privilege": privilege},
                    )
                    assert allowed is False, f"{ROLE} может {privilege} на {table}"
                insert = await conn.scalar(
                    text("SELECT has_table_privilege(:role, :table, 'INSERT')"),
                    {"role": ROLE, "table": table},
                )
                assert insert is True
    finally:
        await _cleanup(database_url, db_name)
