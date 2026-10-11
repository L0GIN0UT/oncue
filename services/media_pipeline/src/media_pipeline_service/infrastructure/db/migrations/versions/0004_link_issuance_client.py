"""Журнал выдачи ссылок: какой сервис запросил ссылку (D13 плана этапа 2).

``link_issuance.client_id`` — ``client_id`` сервисного токена вызывающего (``game``).
Колонка nullable: строки, выданные до сервисных токенов, клиента не знают (журнал живёт
``GV_MEDIA__LINK_ISSUANCE_RETENTION_DAYS``, старые строки уйдут сами). ADD COLUMN без
DEFAULT не переписывает таблицу; CHECK добавляется ``NOT VALID`` и проверяется отдельно,
чтобы не держать ACCESS EXCLUSIVE на время полного сканирования.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("link_issuance", sa.Column("client_id", sa.String(length=64), nullable=True))
    op.execute(
        "ALTER TABLE link_issuance ADD CONSTRAINT ck_link_issuance_client_id_format "
        "CHECK (client_id ~ '^[a-z][a-z0-9_]{0,63}$') NOT VALID"
    )
    op.execute("ALTER TABLE link_issuance VALIDATE CONSTRAINT ck_link_issuance_client_id_format")


def downgrade() -> None:
    # Сырой SQL: op.drop_constraint повторно применил бы naming convention к имени.
    op.execute("ALTER TABLE link_issuance DROP CONSTRAINT ck_link_issuance_client_id_format")
    op.drop_column("link_issuance", "client_id")
