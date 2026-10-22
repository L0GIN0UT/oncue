"""Права онлайна и рейтинга (docs/plans/stage-4.md §2.7, D19).

Только вставка строк справочника по образцу ``0005``/``0007``: admin получает все новые
права, moderator — наблюдение за матчами (D19: наблюдение — модераторы и админы). Аборт
комнат (``match.manage``), публикация ladder-ов и управление сезонами — только admin.
Матрицу, изменённую администратором, миграция не трогает (ON CONFLICT DO NOTHING).

Клиенты сервисных токенов ``match → game (rounds.plan)`` и ``match → media_pipeline
(assets.link)`` в БД не живут: их задаёт ``GV_SERVICE_CLIENTS`` (ADR 0016), источник для
compose — ``SERVICE_CLIENTS`` в ``tools/gv/src/gv_cli/generate.py``.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSIONS = {
    "match.spectate": "Наблюдение за чужими матчами",
    "match.manage": "Админ-API комнат: список активных, принудительное завершение",
    "rating.ladders.publish": "Черновики, публикация и откат ladder-ов рейтинга",
    "rating.seasons.manage": "Создание и закрытие сезонов, пересборка лидербордов",
}
GRANTS = {
    "admin": list(PERMISSIONS),
    "moderator": ["match.spectate"],
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
