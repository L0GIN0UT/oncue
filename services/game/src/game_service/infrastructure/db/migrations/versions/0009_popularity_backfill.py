"""Популярность треков из ``metrics.listens`` (ADR 0074, expand-only).

До этой миграции ``catalog_items.popularity`` заполнялась только из ``metrics.popularity``
(фильмы), у треков она была пустой. Теперь проекция берёт ``popularity``, а при его
отсутствии ``listens``; миграция дополняет уже загруженные строки. Колонка ``popularity`` -
``FLOAT`` (double precision): вмещает и треки (целые до ~1e6), и фильмы (дробные порядка
10-500), расширять тип не нужно. Заполняются только строки без популярности и с числовым
``listens``; остальное не трогается. Откат пустых значений не восстанавливает (данные
производные и пересобираются следующим событием catalog).

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE catalog_items
           SET popularity = (metrics ->> 'listens')::double precision
         WHERE popularity IS NULL
           AND jsonb_typeof(metrics -> 'listens') = 'number'
        """
    )


def downgrade() -> None:
    """Данные производные: откат ничего не делает (значения пересоберёт проекция)."""
