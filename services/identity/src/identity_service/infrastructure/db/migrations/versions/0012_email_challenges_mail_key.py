"""Ключ лимита писем в коде (доработки по ревью безопасности ADR 0041): expand.

Колонка ``email_challenges.mail_key`` (хэш адреса) нужна, чтобы повторная отправка кода
расходовала один и тот же лимит писем на адрес у настоящего кода и у «пустышки».
Nullable: прежние записи (если есть) остаются без ключа.

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("email_challenges", sa.Column("mail_key", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("email_challenges", "mail_key")
