"""UGC-изображения: аватарки на модерации (5b, поток S3; ADR 0048).

Только расширение (expand): новая таблица, существующие не затрагиваются. Инварианты в схеме:
статус из списка; ``pending_key`` задан ровно у ``pending``, ``public_key`` - ровно у
``approved``; форма ключей ``p/<32 hex>.webp`` / ``u/<32 hex>.webp``; ключи уникальны;
``version`` (число решений модератора) не отрицательна. Индексы: очередь
``(status, submitted_at, id)`` и поиск заявок владельца ``(owner_user_id, purpose, status)``.

Вторая таблица, ``ugc_object_purge`` (ключ, ``created_at``), - очередь гарантированного
удаления объектов S3: ключ пишется в транзакции решения, задача relay ``media.ugc.purge``
удаляет объект и стирает строку.

Откат прерывается ошибкой, если в таблицах есть строки (как в progression 0005/0006).

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ugc_images",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("pending_key", sa.String(length=64), nullable=True),
        sa.Column("public_key", sa.String(length=64), nullable=True),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("moderator_id", sa.Uuid(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name=op.f("ck_ugc_images_status_valid"),
        ),
        sa.CheckConstraint("version >= 0", name=op.f("ck_ugc_images_version_non_negative")),
        sa.CheckConstraint(
            "width >= 1 AND height >= 1 AND bytes >= 1",
            name=op.f("ck_ugc_images_dimensions_positive"),
        ),
        sa.CheckConstraint(
            "(status = 'pending') = (pending_key IS NOT NULL)",
            name=op.f("ck_ugc_images_pending_key_iff_pending"),
        ),
        sa.CheckConstraint(
            "(status = 'approved') = (public_key IS NOT NULL)",
            name=op.f("ck_ugc_images_public_key_iff_approved"),
        ),
        sa.CheckConstraint(
            "pending_key IS NULL OR pending_key ~ '^p/[a-f0-9]{32}[.]webp$'",
            name=op.f("ck_ugc_images_pending_key_shape"),
        ),
        sa.CheckConstraint(
            "public_key IS NULL OR public_key ~ '^u/[a-f0-9]{32}[.]webp$'",
            name=op.f("ck_ugc_images_public_key_shape"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ugc_images")),
        sa.UniqueConstraint("pending_key", name=op.f("uq_ugc_images_pending_key")),
        sa.UniqueConstraint("public_key", name=op.f("uq_ugc_images_public_key")),
    )
    op.create_index("ix_ugc_images_queue", "ugc_images", ["status", "submitted_at", "id"])
    op.create_index(
        "ix_ugc_images_owner_purpose_status",
        "ugc_images",
        ["owner_user_id", "purpose", "status"],
    )

    op.create_table(
        "ugc_object_purge",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "key ~ '^[pu]/[a-f0-9]{32}[.]webp$'", name=op.f("ck_ugc_object_purge_key_shape")
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_ugc_object_purge")),
    )
    op.create_index("ix_ugc_object_purge_created_at", "ugc_object_purge", ["created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    images = bind.execute(sa.text("SELECT count(*) FROM ugc_images")).scalar_one()
    queued = bind.execute(sa.text("SELECT count(*) FROM ugc_object_purge")).scalar_one()
    if images or queued:
        raise RuntimeError(
            f"откат 0008 запрещён: ugc_images: {images}, ugc_object_purge: {queued}; "
            "заявки и решения модераторов не восстановить, объекты S3 осиротеют"
        )
    op.drop_table("ugc_object_purge")
    op.drop_table("ugc_images")
