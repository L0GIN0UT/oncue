"""Проекция прав сотрудников для иерархии модераторов (ADR 0058).

Только новая таблица (expand).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-25
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
        "staff_perms_proj",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("perms", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_staff_perms_proj")),
    )


def downgrade() -> None:
    op.drop_table("staff_perms_proj")
