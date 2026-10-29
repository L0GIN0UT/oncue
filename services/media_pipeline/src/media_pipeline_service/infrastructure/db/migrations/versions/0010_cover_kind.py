"""Вид исходника ``cover`` — обложка альбома трека (ADR 0073).

Только расширение (expand): CHECK ``kind_valid`` получает ещё одно значение, столбцы и данные не
меняются. Как и в ``0005``: ограничение добавляется ``NOT VALID`` и проверяется отдельно, но
миграция идёт одной транзакцией, поэтому блокировка таблицы держится до коммита; ``source_media``
невелика (тысячи строк), так что проход проверки занимает миллисекунды.

Откат возвращает прежний CHECK; строки ``cover``, появившиеся после выкладки, откат сам не
удаляет: VALIDATE на них упадёт — такие исходники удаляются вручную до отката (молча стирать
исходники нельзя).

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NAME = "ck_source_media_kind_valid"
_NEW = "kind IN ('audio', 'frame', 'poster', 'soundtrack', 'audio_clip', 'text', 'cover')"
_OLD = "kind IN ('audio', 'frame', 'poster', 'soundtrack', 'audio_clip', 'text')"


def _replace(condition: str) -> None:
    # Сырой SQL: op.drop_constraint повторно применил бы naming convention к имени.
    op.execute(f"ALTER TABLE source_media DROP CONSTRAINT IF EXISTS {_NAME}")
    op.execute(f"ALTER TABLE source_media ADD CONSTRAINT {_NAME} CHECK ({condition}) NOT VALID")
    op.execute(f"ALTER TABLE source_media VALIDATE CONSTRAINT {_NAME}")


def upgrade() -> None:
    _replace(_NEW)


def downgrade() -> None:
    _replace(_OLD)
