"""Наборы и исходники со ``scope`` — свои загадки сборников (план этапа 3b, M1; ADR 0029).

Только расширение (expand):

* ``scope_kind``/``scope_id`` (NULL) у ``source_media`` и ``asset_sets`` — добавление
  nullable-колонок без default меняет только каталог;
* CHECK: пара заполнена целиком, вид владельца из списка, ``scope`` бывает только у текстового
  исходника; происхождение ``collection``. Добавляются ``NOT VALID`` и проверяются отдельно
  (``VALIDATE`` — под SHARE UPDATE EXCLUSIVE, чтение и запись не блокируются);
* таблица ``collections_state`` (версия и статус сборника для отбрасывания старых событий);
* индексы строятся ``CONCURRENTLY`` вне транзакции: частичные уникальные
  ``uq_asset_sets_catalog_profile`` (``scope_id IS NULL`` — прежний инвариант каталожных
  наборов), ``uq_asset_sets_scope_profile``, ``uq_source_media_scope_item`` и обычный
  ``ix_asset_sets_media_item_id`` (наборы единицы любого владельца).

Прежняя ``UNIQUE (media_item_id, clue_type, profile_key, params_hash)`` снимается в этой же
миграции, уже после постройки частичного индекса: иначе набор сборника нельзя завести рядом
с каталожным набором того же профиля. Старый код пишет только каталожные наборы (scope NULL),
для них частичный индекс даёт ровно тот же инвариант — совместимость сохраняется, а снятие
ограничения (без сканирования таблицы) — короткая ACCESS EXCLUSIVE.

Откат удаляет наборы и исходники со scope (в старой схеме их не отличить от каталожных:
старый код выдал бы эмодзи автора в каталожной игре) и возвращает прежние ограничения.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_UNIQUE = "uq_asset_sets_media_item_id_clue_type_profile_key_params_hash"
_SCOPE_CHECKS = {
    "pair": "(scope_kind IS NULL) = (scope_id IS NULL)",
    "kind_valid": "scope_kind IN ('collection')",
}
_SOURCE_ORIGIN_OLD = "origin IN ('upload', 'tmdb', 'seed', 'catalog')"
_SOURCE_ORIGIN_NEW = "origin IN ('upload', 'tmdb', 'seed', 'catalog', 'collection')"
_SCOPED = sa.text("scope_id IS NOT NULL")


def _checks(table: str) -> dict[str, str]:
    checks = {f"ck_{table}_scope_{name}": cond for name, cond in _SCOPE_CHECKS.items()}
    if table == "source_media":
        checks["ck_source_media_scope_text_only"] = "scope_kind IS NULL OR kind = 'text'"
    return checks


def _add_checks(table: str, checks: dict[str, str]) -> None:
    # Сырой SQL: op.create_check_constraint повторно применил бы naming convention к имени.
    for name, condition in checks.items():
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({condition}) NOT VALID")
    for name in checks:
        op.execute(f"ALTER TABLE {table} VALIDATE CONSTRAINT {name}")


def _drop_checks(table: str, names: Sequence[str]) -> None:
    for name in names:
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {name}")


def upgrade() -> None:
    for table in ("source_media", "asset_sets"):
        op.add_column(table, sa.Column("scope_kind", sa.String(length=16), nullable=True))
        op.add_column(table, sa.Column("scope_id", sa.Uuid(), nullable=True))
        _add_checks(table, _checks(table))
    _drop_checks("source_media", ["ck_source_media_origin_valid"])
    _add_checks("source_media", {"ck_source_media_origin_valid": _SOURCE_ORIGIN_NEW})
    op.create_table(
        "collections_state",
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('published', 'hidden', 'deleted')",
            name=op.f("ck_collections_state_status_valid"),
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_collections_state_version_positive")),
        sa.PrimaryKeyConstraint("collection_id", name=op.f("pk_collections_state")),
    )
    with op.get_context().autocommit_block():
        op.create_index(
            "uq_asset_sets_catalog_profile",
            "asset_sets",
            ["media_item_id", "clue_type", "profile_key", "params_hash"],
            unique=True,
            postgresql_where=sa.text("scope_id IS NULL"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.create_index(
            "uq_asset_sets_scope_profile",
            "asset_sets",
            ["scope_kind", "scope_id", "media_item_id", "clue_type", "profile_key", "params_hash"],
            unique=True,
            postgresql_where=_SCOPED,
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.create_index(
            "ix_asset_sets_media_item_id",
            "asset_sets",
            ["media_item_id"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.create_index(
            "uq_source_media_scope_item",
            "source_media",
            ["scope_kind", "scope_id", "media_item_id"],
            unique=True,
            postgresql_where=_SCOPED,
            postgresql_concurrently=True,
            if_not_exists=True,
        )
    op.execute(f"ALTER TABLE asset_sets DROP CONSTRAINT IF EXISTS {_OLD_UNIQUE}")


def downgrade() -> None:
    # Наборы сборников уходят первыми: их уровни — каскадом, ссылки исходников — SET NULL.
    op.execute("DELETE FROM asset_sets WHERE scope_id IS NOT NULL")
    op.execute("DELETE FROM source_media WHERE scope_id IS NOT NULL")
    op.execute(
        f"ALTER TABLE asset_sets ADD CONSTRAINT {_OLD_UNIQUE} "
        "UNIQUE (media_item_id, clue_type, profile_key, params_hash)"
    )
    with op.get_context().autocommit_block():
        for name, table in (
            ("uq_source_media_scope_item", "source_media"),
            ("ix_asset_sets_media_item_id", "asset_sets"),
            ("uq_asset_sets_scope_profile", "asset_sets"),
            ("uq_asset_sets_catalog_profile", "asset_sets"),
        ):
            op.drop_index(name, table_name=table, postgresql_concurrently=True, if_exists=True)
    op.drop_table("collections_state")
    _drop_checks("source_media", ["ck_source_media_origin_valid"])
    _add_checks("source_media", {"ck_source_media_origin_valid": _SOURCE_ORIGIN_OLD})
    for table in ("asset_sets", "source_media"):
        _drop_checks(table, list(_checks(table)))
        op.drop_column(table, "scope_id")
        op.drop_column(table, "scope_kind")
