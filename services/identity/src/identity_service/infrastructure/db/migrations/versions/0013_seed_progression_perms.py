"""Права progression (docs/plans/stage-5.md §2.5, шаг 0a).

Только вставка строк справочника по образцу ``0008``: admin получает все права progression.
Выдача и сторно Версов (``progression.ledger.*``) — зона повышенной безопасности (ledger),
поэтому moderator их не получает. Матрицу, изменённую администратором, миграция не трогает
(ON CONFLICT DO NOTHING).

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSIONS = {
    "progression.config.manage": "Черновики конфига progression, задания и бейджи (чтение)",
    "progression.config.publish": "Публикация и откат конфига progression",
    "progression.ledger.grant": "Ручная выдача Версов",
    "progression.ledger.reverse": "Сторно проводок Версов",
    "progression.ledger.read": "Чтение журнала Версов игрока",
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
