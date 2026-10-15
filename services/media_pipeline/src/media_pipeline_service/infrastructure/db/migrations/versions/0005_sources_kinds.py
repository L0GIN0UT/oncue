"""Новые виды исходников и происхождение (план этапа 3a, M2; D6).

Только расширение (expand):

* ``source_media.position`` (NOT NULL DEFAULT 1 — константа, PostgreSQL ≥ 11 не переписывает
  таблицу), ``source_url``, ``license_note``, ``text_content`` (тексты загадок, ADR 0024);
* CHECK вида (``soundtrack``, ``audio_clip``, ``text``) и происхождения (``seed``,
  ``catalog``) расширяются; ``ready_has_object`` допускает текстовый исходник без
  объекта в хранилище;
* ``asset_sets.source_media_ids`` — все исходники набора (``frame_sequence``); NULL у
  старых строк означает «единственный исходник — ``source_media_id``».

Новые CHECK добавляются ``NOT VALID`` и проверяются отдельно (без долгой ACCESS EXCLUSIVE).
Откат возвращает прежние ограничения и не теряет данных этапа 2. Строки новых видов,
появившиеся после выкладки, откат сам не удаляет: VALIDATE старых CHECK на них упадёт —
такие строки удаляются вручную до отката (осознанно: молча стирать исходники нельзя).

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_CHECKS = {
    "ck_source_media_kind_valid": (
        "kind IN ('audio', 'frame', 'poster', 'soundtrack', 'audio_clip', 'text')"
    ),
    "ck_source_media_origin_valid": "origin IN ('upload', 'tmdb', 'seed', 'catalog')",
    "ck_source_media_ready_has_object": (
        "status <> 'ready' OR (object_key IS NOT NULL AND sha256 IS NOT NULL AND bytes > 0) "
        "OR (kind = 'text' AND text_content IS NOT NULL)"
    ),
    "ck_source_media_position_range": "position BETWEEN 1 AND 100",
    "ck_source_media_text_content_kind": "(kind = 'text') = (text_content IS NOT NULL)",
}
_OLD_CHECKS = {
    "ck_source_media_kind_valid": "kind IN ('audio', 'frame', 'poster')",
    "ck_source_media_origin_valid": "origin IN ('upload', 'tmdb')",
    "ck_source_media_ready_has_object": (
        "status <> 'ready' OR (object_key IS NOT NULL AND sha256 IS NOT NULL AND bytes > 0)"
    ),
}


def _replace_checks(drop: Sequence[str], add: dict[str, str]) -> None:
    # Сырой SQL: op.drop_constraint повторно применил бы naming convention к имени.
    for name in drop:
        op.execute(f"ALTER TABLE source_media DROP CONSTRAINT IF EXISTS {name}")
    for name, condition in add.items():
        op.execute(f"ALTER TABLE source_media ADD CONSTRAINT {name} CHECK ({condition}) NOT VALID")
        op.execute(f"ALTER TABLE source_media VALIDATE CONSTRAINT {name}")


def upgrade() -> None:
    op.add_column(
        "source_media",
        sa.Column("position", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column("source_media", sa.Column("source_url", sa.String(length=2000), nullable=True))
    op.add_column("source_media", sa.Column("license_note", sa.String(length=500), nullable=True))
    op.add_column(
        "source_media",
        sa.Column("text_content", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "asset_sets", sa.Column("source_media_ids", postgresql.ARRAY(sa.Uuid()), nullable=True)
    )
    _replace_checks(list(_OLD_CHECKS), _NEW_CHECKS)


def downgrade() -> None:
    _replace_checks(list(_NEW_CHECKS), _OLD_CHECKS)
    op.drop_column("asset_sets", "source_media_ids")
    op.drop_column("source_media", "text_content")
    op.drop_column("source_media", "license_note")
    op.drop_column("source_media", "source_url")
    op.drop_column("source_media", "position")
