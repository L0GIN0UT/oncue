"""Пакетная загрузка исходников архивом: пакеты и ошибки по файлам.

Только расширение (expand): две новые таблицы, существующие не затрагиваются, данные не
меняются. Инварианты в схеме: статус из списка, счётчики неотрицательны и
``done + failed <= total``. Ошибки по файлам удаляются каскадом вместе с пакетом.

Откат удаляет таблицы (пакеты - служебная история загрузок, исходники живут в ``source_media``).

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_packages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("archive_key", sa.String(length=80), nullable=True),
        sa.Column("archive_sha256", sa.String(length=64), nullable=False),
        sa.Column("archive_bytes", sa.BigInteger(), nullable=False),
        sa.Column("total", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("done", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("failed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("missing_source", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("missing_license", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("errors_dropped", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'done', 'partial', 'failed')",
            name=op.f("ck_import_packages_status_valid"),
        ),
        sa.CheckConstraint(
            "total >= 0 AND done >= 0 AND failed >= 0 AND done + failed <= total",
            name=op.f("ck_import_packages_counters_valid"),
        ),
        sa.CheckConstraint(
            "archive_bytes >= 0", name=op.f("ck_import_packages_archive_bytes_non_negative")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_packages")),
    )
    op.create_index("ix_import_packages_created_at", "import_packages", ["created_at"])
    op.create_table(
        "import_package_errors",
        sa.Column("package_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("item_key", sa.String(length=190), nullable=True),
        sa.Column("path", sa.String(length=300), nullable=True),
        sa.Column("role", sa.String(length=16), nullable=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.ForeignKeyConstraint(
            ["package_id"],
            ["import_packages.id"],
            name=op.f("fk_import_package_errors_package_id_import_packages"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("package_id", "seq", name=op.f("pk_import_package_errors")),
    )


def downgrade() -> None:
    op.drop_table("import_package_errors")
    op.drop_index("ix_import_packages_created_at", table_name="import_packages")
    op.drop_table("import_packages")
