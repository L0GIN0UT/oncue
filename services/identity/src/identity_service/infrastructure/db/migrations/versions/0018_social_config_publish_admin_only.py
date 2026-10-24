"""Публикация конфига social - только admin (5c, ADR 0053).

Решение владельца 2026-10-24 отменяет часть ``0016``: у moderator остаются чтение версий и
черновик (``social.config.manage``), право ``social.config.publish`` снимается. Права
справочника не удаляются - убирается только строка выдачи роли. Применённую ``0016`` не
правим. Матрицу, изменённую администратором вручную, это не защищает от повторной выдачи:
откат возвращает исходную выдачу ``0016``.

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERM = "social.config.publish"
ROLE = "moderator"


def upgrade() -> None:
    op.execute(
        sa.text("DELETE FROM role_permissions WHERE role = :role AND perm = :perm").bindparams(
            role=ROLE, perm=PERM
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO role_permissions (role, perm) "
            "SELECT r.name, :perm FROM roles r WHERE r.name = :role "
            "ON CONFLICT DO NOTHING"
        ).bindparams(role=ROLE, perm=PERM)
    )
