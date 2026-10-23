"""Магазин, инвентарь, оформление (этап 5b, поток S1; ADR 0045, 0046).

Только новые таблицы (expand): ``items``, ``inventory``, ``equipped``; журналы, балансы и
``grants_log`` не меняются. Таблицы пустые, индексы создаются обычным ``CREATE INDEX``.
Инварианты в схеме: у предмета есть условие открытия (``level_required`` или ``price``),
активный предмет имеет картинку, оформление ссылается на предмет из инвентаря игрока.
Откат прерывается ошибкой, если в инвентаре уже есть выдачи (покупки, колесо): удалять
купленное автоматически нельзя.

Цепочка ревизий: ``0004`` → ``0005`` (эта) → ``0006``.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("slot", sa.String(length=64), nullable=False),
        sa.Column("rarity", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=80), nullable=False),
        sa.Column("media_kind", sa.String(length=16), nullable=False),
        sa.Column("asset_key", sa.String(length=100), nullable=True),
        sa.Column("poster_key", sa.String(length=100), nullable=True),
        sa.Column("level_required", sa.Integer(), nullable=True),
        sa.Column("price", sa.BigInteger(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
        sa.CheckConstraint(
            "media_kind IN ('static','animated')", name=op.f("ck_items_media_kind_known")
        ),
        sa.CheckConstraint(
            "level_required IS NOT NULL OR price IS NOT NULL",
            name=op.f("ck_items_unlock_condition_required"),
        ),
        sa.CheckConstraint(
            "level_required IS NULL OR level_required >= 1",
            name=op.f("ck_items_level_required_positive"),
        ),
        sa.CheckConstraint("price IS NULL OR price >= 1", name=op.f("ck_items_price_positive")),
        sa.CheckConstraint(
            "NOT active OR asset_key IS NOT NULL", name=op.f("ck_items_active_has_media")
        ),
        sa.CheckConstraint(
            "media_kind = 'animated' OR poster_key IS NULL",
            name=op.f("ck_items_poster_only_animated"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_items")),
        sa.UniqueConstraint("key", name="uq_items_key"),
    )
    op.create_index("ix_items_slot_active", "items", ["slot", "active"], unique=False)
    op.create_index("ix_items_created", "items", ["created_at", "id"], unique=False)
    op.create_table(
        "inventory",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("acquired_at", _TS, nullable=False),
        sa.Column("expires_at", _TS, nullable=True),
        sa.Column("convert_condition", sa.String(length=200), nullable=True),
        sa.CheckConstraint(
            "source IN ('purchase','level','wheel','promo','pack','admin','event')",
            name=op.f("ck_inventory_source_known"),
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["items.id"], name=op.f("fk_inventory_item_id_items"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("user_id", "item_id", name="pk_inventory"),
    )
    op.create_index("ix_inventory_item", "inventory", ["item_id"], unique=False)
    op.create_table(
        "equipped",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("slot", sa.String(length=64), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("equipped_at", _TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id", "item_id"],
            ["inventory.user_id", "inventory.item_id"],
            name="fk_equipped_inventory",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("user_id", "slot", name="pk_equipped"),
    )
    op.create_index("ix_equipped_user_item", "equipped", ["user_id", "item_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    grants = bind.execute(sa.text("SELECT count(*) FROM inventory")).scalar_one()
    catalog = bind.execute(sa.text("SELECT count(*) FROM items")).scalar_one()
    if grants or catalog:
        raise RuntimeError(
            f"откат 0005 запрещён: в инвентаре {grants} выдач, предметов {catalog}; "
            "каталог и купленное не удаляются автоматически"
        )
    op.drop_index("ix_equipped_user_item", table_name="equipped")
    op.drop_table("equipped")
    op.drop_index("ix_inventory_item", table_name="inventory")
    op.drop_table("inventory")
    op.drop_index("ix_items_created", table_name="items")
    op.drop_index("ix_items_slot_active", table_name="items")
    op.drop_table("items")
