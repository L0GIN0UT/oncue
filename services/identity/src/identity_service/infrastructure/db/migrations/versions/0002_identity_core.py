"""Ядро identity: пользователи, учётные данные, сессии, роли, согласия, привязки.

Только создание таблиц (expand-only), данных не трогает.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "permissions",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_permissions")),
    )
    op.create_table(
        "role_changes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('user_roles', 'matrix')", name=op.f("ck_role_changes_kind_known")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_changes")),
    )
    op.create_index("ix_role_changes_user_id", "role_changes", ["user_id"], unique=False)
    op.create_table(
        "roles",
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_roles")),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("nickname", postgresql.CITEXT(), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("display_name", sa.String(length=40), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("roles_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'deleted') = (deleted_at IS NOT NULL)",
            name=op.f("ck_users_deleted_at_matches"),
        ),
        sa.CheckConstraint("status IN ('active', 'deleted')", name=op.f("ck_users_status_known")),
        sa.CheckConstraint(
            "char_length(nickname::text) BETWEEN 3 AND 48", name=op.f("ck_users_nickname_length")
        ),
        sa.CheckConstraint(
            "version >= 1 AND roles_version >= 0", name=op.f("ck_users_versions_valid")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
        sa.UniqueConstraint("nickname", name=op.f("uq_users_nickname")),
    )
    op.create_table(
        "auth_identities",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("display", sa.String(length=255), nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_auth_identities_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("provider", "subject", name=op.f("pk_auth_identities")),
        sa.UniqueConstraint(
            "user_id", "provider", name=op.f("uq_auth_identities_user_id_provider")
        ),
    )
    op.create_table(
        "consents",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_consents_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "kind", "version", name=op.f("pk_consents")),
    )
    op.create_table(
        "email_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "purpose IN ('verify', 'reset')", name=op.f("ck_email_tokens_purpose_known")
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_email_tokens_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_email_tokens_token_hash")),
    )
    op.create_index("ix_email_tokens_user_id", "email_tokens", ["user_id"], unique=False)
    op.create_table(
        "password_credentials",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_password_credentials_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_password_credentials")),
    )
    op.create_table(
        "role_permissions",
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("perm", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["perm"], ["permissions.name"], name=op.f("fk_role_permissions_perm_permissions")
        ),
        sa.ForeignKeyConstraint(
            ["role"],
            ["roles.name"],
            name=op.f("fk_role_permissions_role_roles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("role", "perm", name=op.f("pk_role_permissions")),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoke_reason", sa.String(length=32), nullable=True),
        sa.Column("ip_prefix", sa.String(length=64), nullable=True),
        sa.CheckConstraint("kind IN ('web', 'admin')", name=op.f("ck_sessions_kind_known")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_sessions_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
    )
    op.create_index(
        "ix_sessions_user_active",
        "sessions",
        ["user_id"],
        unique=False,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["role"], ["roles.name"], name=op.f("fk_user_roles_role_roles")),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_roles_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("user_id", "role", name=op.f("pk_user_roles")),
    )
    op.create_index("ix_user_roles_role", "user_roles", ["role"], unique=False)
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_refresh_tokens_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_tokens_token_hash")),
    )
    op.create_index("ix_refresh_tokens_session_id", "refresh_tokens", ["session_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_refresh_tokens_session_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_index("ix_user_roles_role", table_name="user_roles")
    op.drop_table("user_roles")
    op.drop_index(
        "ix_sessions_user_active",
        table_name="sessions",
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.drop_table("sessions")
    op.drop_table("role_permissions")
    op.drop_table("password_credentials")
    op.drop_index("ix_email_tokens_user_id", table_name="email_tokens")
    op.drop_table("email_tokens")
    op.drop_table("consents")
    op.drop_table("auth_identities")
    op.drop_table("users")
    op.drop_table("roles")
    op.drop_index("ix_role_changes_user_id", table_name="role_changes")
    op.drop_table("role_changes")
    op.drop_table("permissions")
