"""Защита от накрутки через пользовательские сборники (expand-only, ничего не удаляет).

Три добавления, старые данные и проводки не меняются:

* ``collection_authors`` — проекция «сборник → автор, вид» из события
  ``collections.CollectionPublished`` (progression не читает чужую БД). Пустая на старте:
  начисление по сборнику без записи идёт как раньше (владелец неизвестен), а записи
  дополняются новыми событиями и командой догрузки авторов.
* ``collection_play_marks`` — когда игроку последний раз засчитывали награду за сборник
  (ключ «игрок + сборник»); окно повтора задаёт конфиг.
* ``daily_counters.ugc_xp`` — опыт суток с пользовательских сборников (суточный потолок).
  Колонка с ``server_default 0``: существующие строки остаются валидными, откат безопасен.

Права рантайм-роли ``progression_app`` на новые таблицы дают ``DEFAULT PRIVILEGES`` ревизии
``0009``. Откат удаляет только эти объекты.

Цепочка ревизий: ``0012`` -> ``0013`` (эта).

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "collection_authors",
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", _TS, server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('ugc', 'curated')", name=op.f("ck_collection_authors_kind_known")
        ),
        sa.PrimaryKeyConstraint("collection_id", name=op.f("pk_collection_authors")),
    )
    op.create_table(
        "collection_play_marks",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("rewarded_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("user_id", "collection_id", name=op.f("pk_collection_play_marks")),
    )
    op.add_column(
        "daily_counters",
        sa.Column("ugc_xp", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_daily_counters_ugc_xp_non_negative"), "daily_counters", "ugc_xp >= 0"
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_daily_counters_ugc_xp_non_negative"), "daily_counters", type_="check"
    )
    op.drop_column("daily_counters", "ugc_xp")
    op.drop_table("collection_play_marks")
    op.drop_table("collection_authors")
