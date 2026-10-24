"""Права конфига social (5c, ADR 0053): черновик и публикация.

Только вставка строк справочника по образцу ``0013``/``0015``. admin получает оба права;
moderator - тоже оба (решение владельца 2026-10-24: лимиты и порог жалоб крутят во время
спама без деплоя, справочник причин правят модераторы). Опасные значения ограничены
границами схемы ``contracts/social/social_config.v1.json``, публикация привязана к хешу
черновика и пишется в аудит с причиной. Матрицу, изменённую администратором, миграция не
трогает (ON CONFLICT DO NOTHING).

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSIONS = {
    "social.config.manage": "Конфиг social: чтение версий, черновик",
    "social.config.publish": "Конфиг social: публикация и откат",
}
GRANTS = {
    "admin": list(PERMISSIONS),
    "moderator": list(PERMISSIONS),
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
