"""Своя аватарка (этап 5b, поток S3; ADR 0048).

Только новые таблицы (expand): ``avatar_requests`` (заявки по ассетам media-pipeline,
``asset_id`` уникален, ``version`` - последняя применённая версия ``AssetModerated``) и
``user_avatars`` (действующая одобренная аватарка, одна на игрока). Откат прерывается ошибкой,
если в таблицах есть данные: заявки и решения модераторов восстановить нельзя.

Цепочка ревизий: ``0006`` → ``0007`` (эта).

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "avatar_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("submitted_at", _TS, nullable=False),
        sa.Column("decided_at", _TS, nullable=True),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name=op.f("ck_avatar_requests_status_valid"),
        ),
        sa.CheckConstraint("version >= 0", name=op.f("ck_avatar_requests_version_non_negative")),
        sa.CheckConstraint(
            "status <> 'pending' OR reason_code IS NULL",
            name=op.f("ck_avatar_requests_pending_has_no_reason"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_avatar_requests")),
        sa.UniqueConstraint("asset_id", name=op.f("uq_avatar_requests_asset_id")),
    )
    op.create_index(
        "ix_avatar_requests_user_submitted",
        "avatar_requests",
        ["user_id", "submitted_at"],
        unique=False,
    )
    op.create_index(
        "ux_avatar_requests_one_pending",
        "avatar_requests",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "user_avatars",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("public_key", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("approved_at", _TS, nullable=False),
        sa.CheckConstraint(
            "public_key ~ '^u/[a-f0-9]{32}[.]webp$'", name=op.f("ck_user_avatars_public_key_shape")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_user_avatars_version_positive")),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_avatars")),
    )


def downgrade() -> None:
    bind = op.get_bind()
    requests = bind.execute(sa.text("SELECT count(*) FROM avatar_requests")).scalar_one()
    avatars = bind.execute(sa.text("SELECT count(*) FROM user_avatars")).scalar_one()
    if requests or avatars:
        raise RuntimeError(
            f"откат 0007 запрещён: заявок {requests}, действующих аватарок {avatars}; "
            "решения модераторов не восстановить"
        )
    op.drop_table("user_avatars")
    op.drop_index("ux_avatar_requests_one_pending", table_name="avatar_requests")
    op.drop_index("ix_avatar_requests_user_submitted", table_name="avatar_requests")
    op.drop_table("avatar_requests")
