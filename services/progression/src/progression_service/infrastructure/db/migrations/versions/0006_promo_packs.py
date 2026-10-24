"""Паки и промокоды (этап 5b, поток S1; ADR 0045, 0048).

Только новые таблицы (expand): ``packs``, ``pack_items``, ``promo_batches``, ``promo_codes``,
``promo_redemptions``. Код хранится только как HMAC-хеш (``promo_codes.code_hash``), погашение -
PK ``(code_hash, user_id)``; счётчик активаций не выше лимита - ``CHECK``. Откат прерывается
ошибкой, если выпущены коды или есть погашения: открытые коды восстановить нельзя.

Цепочка ревизий: ``0005`` → ``0006`` (эта).

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "packs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=80), nullable=False),
        sa.Column("style", sa.String(length=64), nullable=False),
        sa.Column("verses_amount", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("price_kopecks", sa.BigInteger(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
        sa.CheckConstraint("verses_amount >= 0", name=op.f("ck_packs_verses_non_negative")),
        sa.CheckConstraint("price_kopecks >= 1", name=op.f("ck_packs_price_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_packs")),
        sa.UniqueConstraint("key", name="uq_packs_key"),
    )
    op.create_table(
        "pack_items",
        sa.Column("pack_id", sa.Uuid(), nullable=False),
        sa.Column("item_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint("position >= 0", name=op.f("ck_pack_items_position_non_negative")),
        sa.ForeignKeyConstraint(
            ["pack_id"], ["packs.id"], name=op.f("fk_pack_items_pack_id_packs"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["item_id"], ["items.id"], name=op.f("fk_pack_items_item_id_items"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("pack_id", "item_id", name="pk_pack_items"),
    )
    op.create_index("ix_pack_items_item", "pack_items", ["item_id"], unique=False)
    op.create_table(
        "promo_batches",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("reward_kind", sa.String(length=8), nullable=False),
        sa.Column("verses_amount", sa.BigInteger(), nullable=True),
        sa.Column("item_id", sa.Uuid(), nullable=True),
        sa.Column("max_uses", sa.Integer(), nullable=False),
        sa.Column("per_user_once", sa.Boolean(), nullable=False),
        sa.Column("expires_at", _TS, nullable=True),
        sa.Column("codes_total", sa.Integer(), nullable=False),
        sa.Column("revoked_at", _TS, nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.CheckConstraint(
            "reward_kind IN ('verses','item')", name=op.f("ck_promo_batches_reward_kind_known")
        ),
        sa.CheckConstraint(
            "(reward_kind = 'verses' AND verses_amount >= 1 AND item_id IS NULL)"
            " OR (reward_kind = 'item' AND verses_amount IS NULL AND item_id IS NOT NULL)",
            name=op.f("ck_promo_batches_reward_matches_kind"),
        ),
        sa.CheckConstraint("max_uses >= 1", name=op.f("ck_promo_batches_max_uses_positive")),
        sa.CheckConstraint("codes_total >= 1", name=op.f("ck_promo_batches_codes_total_positive")),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["items.id"],
            name=op.f("fk_promo_batches_item_id_items"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_promo_batches")),
    )
    op.create_index("ix_promo_batches_created", "promo_batches", ["created_at", "id"], unique=False)
    op.create_index("ix_promo_batches_item", "promo_batches", ["item_id"], unique=False)
    op.create_table(
        "promo_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("max_uses", sa.Integer(), nullable=False),
        sa.Column("activations", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.CheckConstraint("length(code_hash) = 64", name=op.f("ck_promo_codes_code_hash_sha256")),
        sa.CheckConstraint(
            "activations >= 0 AND activations <= max_uses",
            name=op.f("ck_promo_codes_activations_within_limit"),
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["promo_batches.id"],
            name=op.f("fk_promo_codes_batch_id_promo_batches"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_promo_codes")),
        sa.UniqueConstraint("code_hash", name="uq_promo_codes_hash"),
        sa.UniqueConstraint("id", "code_hash", "batch_id", name="uq_promo_codes_id_hash_batch"),
    )
    op.create_index("ix_promo_codes_batch", "promo_codes", ["batch_id"], unique=False)
    op.create_table(
        "promo_redemptions",
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("redeemed_at", _TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["code_id", "code_hash", "batch_id"],
            ["promo_codes.id", "promo_codes.code_hash", "promo_codes.batch_id"],
            name="fk_promo_redemptions_code",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("code_hash", "user_id", name="pk_promo_redemptions"),
    )
    op.create_index("ix_promo_redemptions_code", "promo_redemptions", ["code_id"], unique=False)
    op.create_index(
        "ix_promo_redemptions_batch_user",
        "promo_redemptions",
        ["batch_id", "user_id"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    codes = bind.execute(sa.text("SELECT count(*) FROM promo_codes")).scalar_one()
    redemptions = bind.execute(sa.text("SELECT count(*) FROM promo_redemptions")).scalar_one()
    packs = bind.execute(sa.text("SELECT count(*) FROM packs")).scalar_one()
    members = bind.execute(sa.text("SELECT count(*) FROM pack_items")).scalar_one()
    if codes or redemptions or packs or members:
        raise RuntimeError(
            f"откат 0006 запрещён: выпущено кодов {codes}, погашений {redemptions}, "
            f"паков {packs}, строк состава {members}; открытые коды и паки не восстановить"
        )
    op.drop_index("ix_promo_redemptions_code", table_name="promo_redemptions")
    op.drop_index("ix_promo_redemptions_batch_user", table_name="promo_redemptions")
    op.drop_table("promo_redemptions")
    op.drop_index("ix_promo_codes_batch", table_name="promo_codes")
    op.drop_table("promo_codes")
    op.drop_index("ix_promo_batches_item", table_name="promo_batches")
    op.drop_index("ix_promo_batches_created", table_name="promo_batches")
    op.drop_table("promo_batches")
    op.drop_index("ix_pack_items_item", table_name="pack_items")
    op.drop_table("pack_items")
    op.drop_table("packs")
