"""Этап 3b (expand-only): проекция сборников и наборы ассетов со ``scope`` (ADR 0028, 0029).

* новые таблицы ``collection_proj`` и ``collection_items_proj`` (из
  ``collections.collection.v1``);
* ``clue_assets.scope_id`` (NULL — каталожный набор): добавление nullable-колонки без
  default меняет только каталог;
* уникальность ``(media_item_id, clue_type, params_hash, scope_id) NULLS NOT DISTINCT`` —
  индекс строится ``CONCURRENTLY`` вне транзакции; частичный ``ix_clue_assets_scope_id`` —
  для удаления наборов сборника.

Прежний PK ``(media_item_id, clue_type, params_hash)`` снимается в этой же миграции, уже
после постройки нового индекса (как ``0007`` media-pipeline): иначе набор сборника нельзя
завести рядом с каталожным под те же параметры загадки. Для каталожных строк (``scope_id``
NULL) новый индекс даёт ровно прежний инвариант; снятие ограничения — короткая ACCESS
EXCLUSIVE без сканирования таблицы. Окно совместимости: ``ON CONFLICT`` старого кода
(по трём полям) не найдёт арбитра — событие ``AssetsReady`` уйдёт в ретрай consumer-а до
выкладки нового кода (порядок выкладки — §13 плана: game раньше продюсеров).

Откат удаляет наборы со scope (в старой схеме их не отличить от каталожных: старый код
выдал бы эмодзи автора в каталожной игре) и проекцию сборников, возвращает PK.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_PK = "pk_clue_assets"


def upgrade() -> None:
    op.create_table(
        "collection_proj",
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("content_version", sa.Integer(), nullable=False),
        sa.Column("domain", sa.String(16), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("visibility", sa.String(16), nullable=True),
        sa.Column("author_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("collection_id", name=op.f("pk_collection_proj")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_collection_proj_version_positive")),
        sa.CheckConstraint(
            "content_version >= 0", name=op.f("ck_collection_proj_content_version_non_negative")
        ),
        sa.CheckConstraint(
            "domain IN ('music', 'film')", name=op.f("ck_collection_proj_domain_valid")
        ),
        sa.CheckConstraint(
            "status IN ('published', 'hidden', 'deleted')",
            name=op.f("ck_collection_proj_status_valid"),
        ),
        sa.CheckConstraint(
            "visibility IN ('public', 'unlisted')",
            name=op.f("ck_collection_proj_visibility_valid"),
        ),
        sa.CheckConstraint(
            "status = 'deleted' OR (domain IS NOT NULL AND visibility IS NOT NULL)",
            name=op.f("ck_collection_proj_live_has_domain"),
        ),
    )
    op.create_table(
        "collection_items_proj",
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("media_item_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint(
            "collection_id", "position", name=op.f("pk_collection_items_proj")
        ),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["collection_proj.collection_id"],
            name=op.f("fk_collection_items_proj_collection_id_collection_proj"),
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "position >= 0 AND position < 200",
            name=op.f("ck_collection_items_proj_position_valid"),
        ),
    )
    op.add_column("clue_assets", sa.Column("scope_id", sa.Uuid(), nullable=True))
    with op.get_context().autocommit_block():
        op.create_index(
            "uq_clue_assets_scope_key",
            "clue_assets",
            ["media_item_id", "clue_type", "params_hash", "scope_id"],
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.create_index(
            "ix_clue_assets_scope_id",
            "clue_assets",
            ["scope_id"],
            postgresql_where=sa.text("scope_id IS NOT NULL"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
    op.execute(f"ALTER TABLE clue_assets DROP CONSTRAINT IF EXISTS {_OLD_PK}")


def downgrade() -> None:
    op.execute("DELETE FROM clue_assets WHERE scope_id IS NOT NULL")
    op.execute(
        f"ALTER TABLE clue_assets ADD CONSTRAINT {_OLD_PK} "
        "PRIMARY KEY (media_item_id, clue_type, params_hash)"
    )
    with op.get_context().autocommit_block():
        for name in ("ix_clue_assets_scope_id", "uq_clue_assets_scope_key"):
            op.drop_index(
                name, table_name="clue_assets", postgresql_concurrently=True, if_exists=True
            )
    op.drop_column("clue_assets", "scope_id")
    op.drop_table("collection_items_proj")
    op.drop_table("collection_proj")
