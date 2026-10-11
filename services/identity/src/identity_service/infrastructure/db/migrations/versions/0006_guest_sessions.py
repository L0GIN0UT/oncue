"""Гостевые сессии (D5 плана этапа 2): expand-миграция таблицы ``sessions``.

* ``user_id`` становится nullable — снятие NOT NULL меняет только каталог;
* ``subject_kind`` (``user|guest``, default ``user``) и ``guest_id`` — добавление колонки с
  константным default в PostgreSQL 11+ не переписывает таблицу;
* CHECK добавляются ``NOT VALID`` (короткая блокировка) и проверяются отдельно
  (``VALIDATE`` — под SHARE UPDATE EXCLUSIVE, чтение и запись не блокируются);
* индексы строятся ``CONCURRENTLY`` вне транзакции.

Старый код совместим со схемой: он пишет только пользовательские сессии, а для них
``subject_kind`` получает default ``user``. Откат удаляет гостевые сессии (их refresh-токены
уходят каскадом) — иначе ``user_id`` нельзя вернуть в NOT NULL.

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECKS = {
    "ck_sessions_subject_kind_known": "subject_kind IN ('user', 'guest')",
    "ck_sessions_subject_consistent": (
        "(subject_kind = 'user' AND user_id IS NOT NULL AND guest_id IS NULL) OR "
        "(subject_kind = 'guest' AND user_id IS NULL AND guest_id IS NOT NULL AND kind = 'web' "
        "AND last_seen_at IS NOT NULL)"
    ),
}


def upgrade() -> None:
    op.alter_column("sessions", "user_id", existing_type=sa.Uuid(), nullable=True)
    op.add_column(
        "sessions",
        sa.Column(
            "subject_kind", sa.String(length=16), server_default=sa.text("'user'"), nullable=False
        ),
    )
    op.add_column("sessions", sa.Column("guest_id", sa.Uuid(), nullable=True))
    for name, condition in CHECKS.items():
        op.execute(f"ALTER TABLE sessions ADD CONSTRAINT {name} CHECK ({condition}) NOT VALID")
    for name in CHECKS:
        op.execute(f"ALTER TABLE sessions VALIDATE CONSTRAINT {name}")
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_sessions_guest_id",
            "sessions",
            ["guest_id"],
            unique=True,
            postgresql_where=sa.text("guest_id IS NOT NULL"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.create_index(
            "ix_sessions_guest_last_seen",
            "sessions",
            ["last_seen_at"],
            postgresql_where=sa.text("subject_kind = 'guest'"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_sessions_guest_last_seen",
            table_name="sessions",
            postgresql_concurrently=True,
            if_exists=True,
        )
        op.drop_index(
            "ix_sessions_guest_id",
            table_name="sessions",
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.execute("DELETE FROM sessions WHERE user_id IS NULL")
    for name in reversed(CHECKS):
        op.drop_constraint(op.f(name), "sessions", type_="check")
    op.drop_column("sessions", "guest_id")
    op.drop_column("sessions", "subject_kind")
    op.alter_column("sessions", "user_id", existing_type=sa.Uuid(), nullable=False)
