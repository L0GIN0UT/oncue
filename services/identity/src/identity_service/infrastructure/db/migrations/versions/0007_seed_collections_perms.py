"""Права модерации и курации сборников (docs/plans/stage-3b.md §2.8, ADR 0030).

Только вставка строк справочника по образцу ``0005``: admin получает все новые права,
moderator — просмотр и скрытие сборников. Матрицу, изменённую администратором, миграция не
трогает (добавляет недостающие пары, ON CONFLICT DO NOTHING). Логика выдачи токенов не
меняется: права попадают в claim ``perms`` из ``role_permissions`` как и раньше.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSIONS = {
    "collections.view": "Просмотр сборников, включая скрытые, и их состава",
    "collections.curate": "Кураторские сборники и отметка «рекомендуемое»",
    "collections.unpublish": "Скрытие и показ сборников",
    "collections.restrict_author": "Ограничение автора сборников",
}
GRANTS = {
    "admin": list(PERMISSIONS),
    "moderator": ["collections.view", "collections.unpublish"],
}


def upgrade() -> None:
    bind = op.get_bind()
    for name, description in PERMISSIONS.items():
        bind.execute(
            sa.text(
                "INSERT INTO permissions (name, description) VALUES (:name, :description) "
                "ON CONFLICT DO NOTHING"
            ),
            {"name": name, "description": description},
        )
    for role, perms in GRANTS.items():
        for perm in perms:
            bind.execute(
                sa.text(
                    "INSERT INTO role_permissions (role, perm) "
                    "SELECT r.name, :perm FROM roles r WHERE r.name = :role "
                    "ON CONFLICT DO NOTHING"
                ),
                {"role": role, "perm": perm},
            )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM role_permissions WHERE perm IN :perms").bindparams(
            sa.bindparam("perms", list(PERMISSIONS), expanding=True)
        )
    )
    op.execute(
        sa.text("DELETE FROM permissions WHERE name IN :perms").bindparams(
            sa.bindparam("perms", list(PERMISSIONS), expanding=True)
        )
    )
