"""Тексты загадок, страны, происхождение исходников, переопубликация (план этапа 3a, M7–M9).

Только расширение (expand):

* ``media_items.countries`` (``NOT NULL DEFAULT '{}'`` — константа, таблица не
  переписывается) и ``media_items.clue_texts`` (одобренный снимок, ADR 0024);
* ``source_media_refs``: ``kind_position`` (DEFAULT 1), ``source_url``, ``license_note``
  (D6); CHECK вида расширяется на ``soundtrack`` и ``audio_clip``;
* новые таблицы ``clue_texts`` (черновики и ревизии) и ``republish_jobs``.

Новые и заменённые CHECK добавляются ``NOT VALID`` и проверяются отдельно. Откат не
теряет данных этапа 2; исходники новых видов перед откатом удаляются вручную (VALIDATE
старого CHECK на них упадёт — молча стирать данные нельзя).

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KIND_NEW = "kind IN ('audio', 'frame', 'poster', 'soundtrack', 'audio_clip')"
_KIND_OLD = "kind IN ('audio', 'frame', 'poster')"


def _check(name: str, condition: str) -> None:
    op.execute(f"ALTER TABLE source_media_refs ADD CONSTRAINT {name} CHECK ({condition}) NOT VALID")
    op.execute(f"ALTER TABLE source_media_refs VALIDATE CONSTRAINT {name}")


def upgrade() -> None:
    op.add_column(
        "media_items",
        sa.Column(
            "countries",
            postgresql.ARRAY(sa.String(length=2)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.add_column(
        "media_items",
        sa.Column("clue_texts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "source_media_refs",
        sa.Column("kind_position", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column(
        "source_media_refs", sa.Column("source_url", sa.String(length=2000), nullable=True)
    )
    op.add_column(
        "source_media_refs", sa.Column("license_note", sa.String(length=500), nullable=True)
    )
    # Сырой SQL: op.drop_constraint повторно применил бы naming convention к имени.
    op.execute("ALTER TABLE source_media_refs DROP CONSTRAINT ck_source_media_refs_kind_known")
    _check("ck_source_media_refs_kind_known", _KIND_NEW)
    _check("ck_source_media_refs_kind_position_range", "kind_position BETWEEN 1 AND 100")

    op.create_table(
        "clue_texts",
        sa.Column("media_item_id", sa.Uuid(), nullable=False),
        sa.Column("plot_raw", sa.Text(), nullable=True),
        sa.Column("plot_masked", sa.String(length=2000), nullable=True),
        sa.Column("tagline", sa.String(length=300), nullable=True),
        sa.Column("emoji", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("approved_revision", sa.Integer(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'approved')", name=op.f("ck_clue_texts_status_known")
        ),
        sa.CheckConstraint("revision >= 0", name=op.f("ck_clue_texts_revision_non_negative")),
        sa.CheckConstraint(
            "approved_revision IS NULL OR approved_revision BETWEEN 1 AND revision",
            name=op.f("ck_clue_texts_approved_revision_range"),
        ),
        sa.CheckConstraint(
            "plot_raw IS NULL OR length(plot_raw) <= 5000",
            name=op.f("ck_clue_texts_plot_raw_length"),
        ),
        sa.ForeignKeyConstraint(
            ["media_item_id"],
            ["media_items.id"],
            name=op.f("fk_clue_texts_media_item_id_media_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("media_item_id", name=op.f("pk_clue_texts")),
    )
    op.create_table(
        "republish_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("cursor", sa.Uuid(), nullable=True),
        sa.Column("published", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "domain IS NULL OR domain IN ('music', 'film')",
            name=op.f("ck_republish_jobs_domain_known"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'done')", name=op.f("ck_republish_jobs_status_known")
        ),
        sa.CheckConstraint("published >= 0", name=op.f("ck_republish_jobs_published_non_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_republish_jobs")),
    )


def downgrade() -> None:
    op.drop_table("republish_jobs")
    op.drop_table("clue_texts")
    op.execute(
        "ALTER TABLE source_media_refs DROP CONSTRAINT ck_source_media_refs_kind_position_range"
    )
    op.execute("ALTER TABLE source_media_refs DROP CONSTRAINT ck_source_media_refs_kind_known")
    _check("ck_source_media_refs_kind_known", _KIND_OLD)
    op.drop_column("source_media_refs", "license_note")
    op.drop_column("source_media_refs", "source_url")
    op.drop_column("source_media_refs", "kind_position")
    op.drop_column("media_items", "clue_texts")
    op.drop_column("media_items", "countries")
