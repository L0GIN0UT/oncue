"""Права 5c: очередь модерации, санкции, удаление чужих комментариев (план этапа 5, §10.1).

Только вставка строк справочника по образцу ``0014``. admin получает все четыре права;
moderator - очередь, удаление комментариев и санкции ``ranked``/``chat``/``online``. Бан
аккаунта (``social.sanctions.account``) - только admin: он закрывает вход. Матрицу, изменённую
администратором, миграция не трогает (ON CONFLICT DO NOTHING).

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSIONS = {
    "social.moderate": "Очередь модерации: взять в работу, решить, эскалировать",
    "social.comments.delete_any": "Удаление чужих комментариев",
    "social.sanctions.manage": "Санкции ranked, chat, online: выдача и снятие",
    "social.sanctions.account": "Санкция account (бан входа): выдача и снятие",
}
GRANTS = {
    "admin": list(PERMISSIONS),
    "moderator": [
        "social.moderate",
        "social.comments.delete_any",
        "social.sanctions.manage",
    ],
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
