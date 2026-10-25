"""Права сезонных событий progression (5d, ADR 0054, шаг 0a).

Вставка строк справочника по образцу ``0013``: оба права получает только admin
(``publish`` - публикация и досрочная остановка запуска, решение владельца 2026-10-24).
Матрицу, изменённую администратором, миграция не трогает (ON CONFLICT DO NOTHING).

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSIONS = {
    "progression.events.manage": "События progression: определения, запуски, статистика",
    "progression.events.publish": "Публикация и досрочная остановка запуска события",
}
GRANTS = {"admin": list(PERMISSIONS)}


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
