"""Права конфигов режимов и наборов дня game (docs/plans/stage-2.md §2.7).

Только вставка строк справочника: admin получает все новые права, moderator — просмотр
конфигов и курацию наборов дня. Матрицу, изменённую администратором, миграция не трогает
(добавляет недостающие пары, ON CONFLICT DO NOTHING).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSIONS = {
    "config.modes.view": "Просмотр конфигов режимов",
    "config.modes.edit": "Правка черновиков конфигов режимов",
    "config.modes.publish": "Публикация, откат и архив конфигов режимов",
    "game.daily.view": "Просмотр наборов дня",
    "game.daily.curate": "Замена единиц и перегенерация наборов дня",
}
GRANTS = {
    "admin": list(PERMISSIONS),
    "moderator": ["config.modes.view", "game.daily.view", "game.daily.curate"],
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
