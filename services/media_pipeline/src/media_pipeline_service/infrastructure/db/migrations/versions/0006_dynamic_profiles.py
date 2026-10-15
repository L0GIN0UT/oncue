"""Динамические профили ассетов (план этапа 3a, M5; ADR 0025).

Только расширение: новая таблица ``asset_profiles_dynamic`` и индекс
``asset_sets (clue_type, params_hash)`` для счётчиков ``GET /admin/v1/profiles``. Индекс
строится обычным ``CREATE INDEX``: на этапе 3a в ``asset_sets`` порядка тысяч строк, а
``CONCURRENTLY`` нельзя выполнить в транзакции миграции; при росте таблицы такие индексы
создаются отдельной нетранзакционной миграцией.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asset_profiles_dynamic",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("clue_type", sa.String(length=64), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=False),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("params_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("mode_key", sa.String(length=100), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("backfill_cursor", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('backfilling', 'active', 'rejected')",
            name=op.f("ck_asset_profiles_dynamic_status_valid"),
        ),
        sa.CheckConstraint(
            "domain IN ('music', 'film')", name=op.f("ck_asset_profiles_dynamic_domain_valid")
        ),
        sa.CheckConstraint(
            "params_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_asset_profiles_dynamic_params_hash_sha256"),
        ),
        sa.CheckConstraint(
            "(status = 'rejected') = (error_code IS NOT NULL)",
            name=op.f("ck_asset_profiles_dynamic_rejected_has_code"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_asset_profiles_dynamic")),
        sa.UniqueConstraint(
            "clue_type",
            "params_hash",
            "domain",
            name=op.f("uq_asset_profiles_dynamic_clue_type_params_hash_domain"),
        ),
    )
    op.create_index(
        "ix_asset_profiles_dynamic_status", "asset_profiles_dynamic", ["status"], unique=False
    )
    op.create_index(
        "ix_asset_sets_clue_type_params_hash",
        "asset_sets",
        ["clue_type", "params_hash"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_asset_sets_clue_type_params_hash", table_name="asset_sets")
    op.drop_index("ix_asset_profiles_dynamic_status", table_name="asset_profiles_dynamic")
    op.drop_table("asset_profiles_dynamic")
