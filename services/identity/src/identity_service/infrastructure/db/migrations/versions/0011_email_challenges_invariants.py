"""Инварианты кодов в схеме и индексы очистки (ревью безопасности ADR 0041): expand.

CHECK-ограничения на ``email_challenges`` (попытки не превышают лимит, срок позже создания,
код закрывается один раз, у login/password_change есть пользователь), индекс по ``user_id``
(каскад и удаление кодов аккаунта) и индексы под очистку ``trusted_devices``.

Таблицы свежие (0009 из этой же ветки): проверка CHECK при создании короткая. Вне dev при
большой таблице добавлять ``NOT VALID`` и валидировать отдельно.

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CHECKS = {
    "ck_email_challenges_attempts_within_max": "attempts <= max_attempts",
    "ck_email_challenges_spent_covers_attempts": "spent_total >= attempts",
    "ck_email_challenges_expires_after_created": "expires_at > created_at",
    "ck_email_challenges_closed_once": "NOT (consumed_at IS NOT NULL AND cancelled_at IS NOT NULL)",
    "ck_email_challenges_user_bound_purposes_have_user": (
        "purpose NOT IN ('login', 'password_change') OR user_id IS NOT NULL"
    ),
}


def upgrade() -> None:
    for name, expression in CHECKS.items():
        op.create_check_constraint(op.f(name), "email_challenges", expression)
    op.create_index(
        "ix_email_challenges_user_id",
        "email_challenges",
        ["user_id"],
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_index("ix_trusted_devices_expires_at", "trusted_devices", ["expires_at"])
    op.create_index(
        "ix_trusted_devices_revoked_at",
        "trusted_devices",
        ["revoked_at"],
        postgresql_where=sa.text("revoked_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_trusted_devices_revoked_at", table_name="trusted_devices")
    op.drop_index("ix_trusted_devices_expires_at", table_name="trusted_devices")
    op.drop_index("ix_email_challenges_user_id", table_name="email_challenges")
    for name in reversed(list(CHECKS)):
        op.drop_constraint(op.f(name), "email_challenges", type_="check")
