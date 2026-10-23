"""Права 5b: предметы, промокоды, модерация UGC-изображений, рассылка (план этапа 5, §9.2).

Только вставка строк справочника по образцу ``0013``. admin получает все четыре права;
moderator - только ``media.ugc.moderate`` (очередь «UGC-изображения»). Предметы и промокоды
(валюта) и рассылка - только admin. Матрицу, изменённую администратором, миграция не трогает
(ON CONFLICT DO NOTHING).

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSIONS = {
    "progression.items.manage": "Предметы и паки косметики, загрузка их медиа",
    "progression.promo.manage": "Промокоды: генерация партий, отзыв",
    "media.ugc.moderate": "Модерация UGC-изображений (аватарки, обложки сборников)",
    "notifications.broadcast.send": "Информационная рассылка подписчикам",
}
GRANTS = {
    "admin": list(PERMISSIONS),
    "moderator": ["media.ugc.moderate"],
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
