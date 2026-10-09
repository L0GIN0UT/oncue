"""Справочник прав и роли user, moderator, admin (docs/plans/stage-1.md §2.6).

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSIONS = {
    "admin.access": "Вход в админ-панель",
    "users.view": "Просмотр пользователей",
    "roles.assign": "Назначение ролей пользователям",
    "roles.manage": "Изменение матрицы ролей",
    "catalog.import": "Импорт в каталог",
    "catalog.block_item": "Блокировка единиц каталога",
    "catalog.edit_aliases": "Правка алиасов",
    "media.upload_source": "Загрузка исходников медиа",
    "media.regenerate": "Перегенерация ассетов",
}
ROLES = {
    "user": [],
    "moderator": ["admin.access", "users.view", "catalog.block_item", "catalog.edit_aliases"],
    "admin": list(PERMISSIONS),
}

_permissions = sa.table("permissions", sa.column("name"), sa.column("description"))
_roles = sa.table("roles", sa.column("name"))
_role_permissions = sa.table("role_permissions", sa.column("role"), sa.column("perm"))


def upgrade() -> None:
    op.bulk_insert(_permissions, [{"name": k, "description": v} for k, v in PERMISSIONS.items()])
    op.bulk_insert(_roles, [{"name": name} for name in ROLES])
    op.bulk_insert(
        _role_permissions,
        [{"role": role, "perm": perm} for role, perms in ROLES.items() for perm in perms],
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM role_permissions WHERE role IN :roles").bindparams(
            sa.bindparam("roles", list(ROLES), expanding=True)
        )
    )
    op.execute(
        sa.text("DELETE FROM roles WHERE name IN :roles").bindparams(
            sa.bindparam("roles", list(ROLES), expanding=True)
        )
    )
    op.execute(
        sa.text("DELETE FROM permissions WHERE name IN :perms").bindparams(
            sa.bindparam("perms", list(PERMISSIONS), expanding=True)
        )
    )
