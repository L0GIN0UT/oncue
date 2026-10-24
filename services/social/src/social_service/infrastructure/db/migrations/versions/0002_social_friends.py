"""Друзья, блокировки, приватность, проекции и версии конфига (5c, поток F1).

Только новые объекты (expand): существующие таблицы (outbox/inbox) не меняются. Откат
удаляет эти таблицы целиком (данные друзей будут потеряны - выкат назад допустим только до
первого использования функции).

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)
_JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response", _JSONB, nullable=False),
        sa.Column("status_code", sa.SmallInteger(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.CheckConstraint(
            "length(request_hash) = 64", name=op.f("ck_idempotency_keys_request_hash_sha256")
        ),
        sa.CheckConstraint(
            "status_code BETWEEN 200 AND 299", name=op.f("ck_idempotency_keys_status_code_success")
        ),
        sa.PrimaryKeyConstraint("actor_id", "key", name=op.f("pk_idempotency_keys")),
    )
    op.create_index(
        "ix_idempotency_keys_created_at", "idempotency_keys", ["created_at"], unique=False
    )

    op.create_table(
        "config_versions",
        sa.Column("version", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("body", _JSONB, nullable=False),
        sa.Column("body_sha256", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("published_at", _TS, nullable=True),
        sa.CheckConstraint("version >= 1", name=op.f("ck_config_versions_version_positive")),
        sa.CheckConstraint(
            "status IN ('draft','published','archived')",
            name=op.f("ck_config_versions_status_known"),
        ),
        sa.CheckConstraint(
            "status <> 'published' OR published_at IS NOT NULL",
            name=op.f("ck_config_versions_published_dated"),
        ),
        sa.CheckConstraint(
            "length(body_sha256) = 64", name=op.f("ck_config_versions_body_sha256_len")
        ),
        sa.PrimaryKeyConstraint("version", name=op.f("pk_config_versions")),
    )
    op.create_index(
        "uq_config_versions_published",
        "config_versions",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_index(
        "uq_config_versions_draft",
        "config_versions",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'draft'"),
    )

    op.create_table(
        "friend_requests",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("from_user_id", sa.Uuid(), nullable=False),
        sa.Column("to_user_id", sa.Uuid(), nullable=False),
        sa.Column("user_a", sa.Uuid(), nullable=False),
        sa.Column("user_b", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.Column("resolved_at", _TS, nullable=True),
        sa.CheckConstraint("user_a < user_b", name=op.f("ck_friend_requests_pair_ordered")),
        sa.CheckConstraint("from_user_id <> to_user_id", name=op.f("ck_friend_requests_not_self")),
        sa.CheckConstraint(
            "status IN ('pending','accepted','declined','cancelled')",
            name=op.f("ck_friend_requests_status_known"),
        ),
        sa.CheckConstraint(
            "(from_user_id = user_a AND to_user_id = user_b) "
            "OR (from_user_id = user_b AND to_user_id = user_a)",
            name=op.f("ck_friend_requests_pair_matches_direction"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_friend_requests")),
    )
    op.create_index(
        "uq_friend_requests_pending_pair",
        "friend_requests",
        ["user_a", "user_b"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_index(
        "ix_friend_requests_from_created", "friend_requests", ["from_user_id", "created_at"]
    )
    op.create_index(
        "ix_friend_requests_to_pending",
        "friend_requests",
        ["to_user_id"],
        postgresql_where=sa.text("status = 'pending'"),
    )

    op.create_table(
        "friendships",
        sa.Column("user_a", sa.Uuid(), nullable=False),
        sa.Column("user_b", sa.Uuid(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.CheckConstraint("user_a < user_b", name=op.f("ck_friendships_pair_ordered")),
        sa.PrimaryKeyConstraint("user_a", "user_b", name=op.f("pk_friendships")),
    )
    op.create_index("ix_friendships_user_b", "friendships", ["user_b"])

    op.create_table(
        "friend_codes",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=8), nullable=False),
        sa.Column("rotated_at", _TS, nullable=False),
        sa.CheckConstraint("length(code) = 8", name=op.f("ck_friend_codes_code_len")),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_friend_codes")),
        sa.UniqueConstraint("code", name=op.f("uq_friend_codes_code")),
    )

    op.create_table(
        "blocks",
        sa.Column("blocker_id", sa.Uuid(), nullable=False),
        sa.Column("blocked_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", _TS, nullable=False),
        sa.CheckConstraint("blocker_id <> blocked_id", name=op.f("ck_blocks_not_self")),
        sa.PrimaryKeyConstraint("blocker_id", "blocked_id", name=op.f("pk_blocks")),
    )
    op.create_index("ix_blocks_blocked", "blocks", ["blocked_id"])

    op.create_table(
        "privacy_settings",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("profile_public", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("stats_public", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "friend_requests", sa.String(length=8), server_default=sa.text("'all'"), nullable=False
        ),
        sa.Column("updated_at", _TS, nullable=False),
        sa.CheckConstraint(
            "friend_requests IN ('all','none')",
            name=op.f("ck_privacy_settings_friend_requests_known"),
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_privacy_settings")),
    )

    op.create_table(
        "users_proj",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("nickname", sa.String(length=24), nullable=False),
        sa.Column("nickname_key", sa.String(length=24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("updated_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_users_proj")),
    )
    op.create_index("ix_users_proj_nickname_key", "users_proj", ["nickname_key"])

    op.create_table(
        "in_match_proj",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("match_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", _TS, nullable=False),
        sa.PrimaryKeyConstraint("user_id", "match_id", name=op.f("pk_in_match_proj")),
    )
    op.create_index("ix_in_match_proj_match", "in_match_proj", ["match_id"])


def downgrade() -> None:
    op.drop_index("ix_in_match_proj_match", table_name="in_match_proj")
    op.drop_table("in_match_proj")
    op.drop_index("ix_users_proj_nickname_key", table_name="users_proj")
    op.drop_table("users_proj")
    op.drop_table("privacy_settings")
    op.drop_index("ix_blocks_blocked", table_name="blocks")
    op.drop_table("blocks")
    op.drop_table("friend_codes")
    op.drop_index("ix_friendships_user_b", table_name="friendships")
    op.drop_table("friendships")
    op.drop_index(
        "ix_friend_requests_to_pending",
        table_name="friend_requests",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_index("ix_friend_requests_from_created", table_name="friend_requests")
    op.drop_index(
        "uq_friend_requests_pending_pair",
        table_name="friend_requests",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_table("friend_requests")
    op.drop_index(
        "uq_config_versions_draft",
        table_name="config_versions",
        postgresql_where=sa.text("status = 'draft'"),
    )
    op.drop_index(
        "uq_config_versions_published",
        table_name="config_versions",
        postgresql_where=sa.text("status = 'published'"),
    )
    op.drop_table("config_versions")
    op.drop_index("ix_idempotency_keys_created_at", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
