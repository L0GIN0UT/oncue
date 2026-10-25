"""Права рантайм-роли ``progression_app`` (ADR 0060): журналы только на чтение и вставку.

Миграции выполняет владелец схемы (``progression``), api/relay/consumer ходят ролью
``progression_app``. Здесь владелец выдаёт ей DML на таблицы, но отбирает ``UPDATE``/``DELETE``/
``TRUNCATE`` на журналах (``xp_ledger``, ``currency_ledger``, ``admin_actions``) и любую запись в
``alembic_version``: роль не владеет таблицами, поэтому не может ни изменить журнал, ни отключить
его триггер append-only. Права на будущие таблицы — через ``ALTER DEFAULT PRIVILEGES`` (новый
журнал обязан сам отозвать лишнее в своей миграции).

Роль создаёт скрипт инициализации Postgres (``10-service-databases.sh``) или оператор; в
окружении без роли (тесты, локальная БД одной ролью) миграция ничего не делает. Меняются только
права, данные и схема не затрагиваются, откат безопасен.

Цепочка ревизий: ``0008`` → ``0009`` (эта).

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UPGRADE = """
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'progression_app') THEN
    GRANT USAGE ON SCHEMA public TO progression_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO progression_app;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO progression_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
      GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO progression_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO progression_app;
    REVOKE UPDATE, DELETE, TRUNCATE
      ON xp_ledger, currency_ledger, admin_actions FROM progression_app;
    REVOKE ALL ON alembic_version FROM progression_app;
  END IF;
END
$$
"""

_DOWNGRADE = """
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'progression_app') THEN
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
      REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM progression_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA public
      REVOKE USAGE, SELECT ON SEQUENCES FROM progression_app;
    REVOKE ALL ON ALL TABLES IN SCHEMA public FROM progression_app;
    REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM progression_app;
  END IF;
END
$$
"""


def upgrade() -> None:
    op.execute(_UPGRADE)


def downgrade() -> None:
    op.execute(_DOWNGRADE)
