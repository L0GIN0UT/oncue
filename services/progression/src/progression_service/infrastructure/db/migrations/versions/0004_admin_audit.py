"""Admin-API progression: аудит действий, ключи идемпотентности, «черновик один» (поток P3).

Только новые объекты (expand): таблицы ``admin_actions`` и ``idempotency_keys`` и частичный
уникальный индекс ``uq_config_versions_draft`` на ``config_versions`` (таблица маленькая —
обычный ``CREATE INDEX``). Журналы и балансы не меняются. ``admin_actions`` append-only
(триггеры UPDATE/DELETE построчно и TRUNCATE; функция - из ``0002``). Откат удаляет только эти
объекты и прерывается ошибкой, если в аудите уже есть записи.

Цепочка ревизий: ``0002`` → ``0003`` (P2) → ``0004`` (эта).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSONB = postgresql.JSONB(astext_type=sa.Text())
_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "admin_actions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=200), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("details", _JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_actions")),
    )
    op.create_index("ix_admin_actions_target_at", "admin_actions", ["target", "at"], unique=False)
    op.create_index("ix_admin_actions_actor_at", "admin_actions", ["actor_id", "at"], unique=False)
    op.create_table(
        "idempotency_keys",
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response", _JSONB, nullable=False),
        sa.Column("status_code", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.CheckConstraint(
            "length(request_hash) = 64", name=op.f("ck_idempotency_keys_request_hash_sha256")
        ),
        sa.CheckConstraint(
            "status_code BETWEEN 200 AND 299", name=op.f("ck_idempotency_keys_status_code_success")
        ),
        sa.PrimaryKeyConstraint("actor_id", "key", name=op.f("pk_idempotency_keys")),
    )
    op.create_index(
        "ix_idempotency_keys_created_at", "idempotency_keys", ["created_at"], unique=False
    )
    op.create_index(
        "uq_config_versions_draft",
        "config_versions",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'draft'"),
    )
    # Аудит append-only, как журналы: функция триггера создана миграцией 0002.
    op.execute(
        "CREATE TRIGGER trg_admin_actions_append_only BEFORE UPDATE OR DELETE ON admin_actions "
        "FOR EACH ROW EXECUTE FUNCTION progression_ledger_forbid_mutation()"
    )
    op.execute(
        "CREATE TRIGGER trg_admin_actions_no_truncate BEFORE TRUNCATE ON admin_actions "
        "FOR EACH STATEMENT EXECUTE FUNCTION progression_ledger_forbid_mutation()"
    )


def downgrade() -> None:
    count = op.get_bind().execute(sa.text("SELECT count(*) FROM admin_actions")).scalar_one()
    if count:
        raise RuntimeError(
            f"откат 0004 запрещён: в аудите {count} записей; аудит не удаляется автоматически"
        )
    op.execute("DROP TRIGGER IF EXISTS trg_admin_actions_no_truncate ON admin_actions")
    op.execute("DROP TRIGGER IF EXISTS trg_admin_actions_append_only ON admin_actions")
    op.drop_index(
        "uq_config_versions_draft",
        table_name="config_versions",
        postgresql_where=sa.text("status = 'draft'"),
    )
    op.drop_index("ix_idempotency_keys_created_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
    op.drop_index("ix_admin_actions_actor_at", table_name="admin_actions")
    op.drop_index("ix_admin_actions_target_at", table_name="admin_actions")
    op.drop_table("admin_actions")
