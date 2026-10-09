"""Агрегат User, матрица ролей, сессии и ротация refresh-токенов."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from identity_service.domain import (
    DisplayName,
    EmailAddress,
    ExternalAccountLinked,
    ExternalAccountUnlinked,
    Nickname,
    Provider,
    RefreshToken,
    RoleMatrix,
    Session,
    SessionKind,
    User,
    UserDeleted,
    UserRegistered,
    UserRolesChanged,
    UserStatus,
    rotate_refresh_token,
)
from identity_service.domain.session import ReuseDetected, Rotated

from gv_core import DomainError, InvalidInputError, UnauthenticatedError

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)

MATRIX = RoleMatrix(
    roles={
        "user": frozenset(),
        "moderator": frozenset({"admin.access", "users.view", "catalog.block_item"}),
        "admin": frozenset({"admin.access", "users.view", "roles.assign", "roles.manage"}),
    },
    permissions=frozenset(
        {"admin.access", "users.view", "catalog.block_item", "roles.assign", "roles.manage"}
    ),
)


def _user() -> User:
    return User.register(
        uuid.uuid4(),
        nickname=Nickname.parse("neo_2026"),
        email=EmailAddress.parse("neo@example.com"),
        display_name=DisplayName.parse("Neo"),
        locale="ru",
        roles=frozenset({"user"}),
        now=NOW,
    )


def test_register_records_event_without_pii() -> None:
    user = _user()
    (event,) = user.pending_events
    assert isinstance(event, UserRegistered)
    payload = event.payload()
    assert payload == {
        "user_id": str(user.id),
        "nickname": "neo_2026",
        "display_name": "Neo",
        "locale": "ru",
        "version": 1,
    }
    assert "email" not in str(payload)
    assert event.aggregate_id == str(user.id)
    assert user.version == 1
    assert user.status is UserStatus.ACTIVE
    assert not user.email_verified


def test_verify_email_is_idempotent() -> None:
    user = _user()
    assert user.verify_email(NOW)
    assert not user.verify_email(NOW + timedelta(hours=1))
    assert user.email_verified_at == NOW


def test_change_roles_emits_full_snapshot_and_bumps_versions() -> None:
    user = _user()
    user.pull_events()
    actor = uuid.uuid4()
    assert user.change_roles(frozenset({"moderator"}), MATRIX, actor_id=actor, reason="набор")
    (event,) = user.pull_events()
    assert isinstance(event, UserRolesChanged)
    assert event.payload() == {
        "user_id": str(user.id),
        "roles": ["moderator"],
        "perms": ["admin.access", "catalog.block_item", "users.view"],
        "version": 2,
        "actor_id": str(actor),
        "reason": "набор",
    }
    assert user.roles_version == 1
    # Повтор с тем же набором — ничего не меняет и не публикует.
    assert not user.change_roles(frozenset({"moderator"}), MATRIX, actor_id=actor, reason="x")
    assert user.pull_events() == []


def test_change_roles_rejects_unknown_role() -> None:
    with pytest.raises(InvalidInputError) as info:
        _user().change_roles(frozenset({"god"}), MATRIX, actor_id=None, reason="x")
    assert info.value.code == "roles.unknown_role"


def test_effective_perms_and_grant_rule() -> None:
    assert MATRIX.effective_perms({"user", "moderator"}) == {
        "admin.access",
        "users.view",
        "catalog.block_item",
    }
    moderator_perms = MATRIX.effective_perms({"moderator"})
    assert MATRIX.can_grant(moderator_perms, {"moderator"})
    assert not MATRIX.can_grant(moderator_perms, {"admin"})  # нельзя выдать больше, чем есть


def test_matrix_validation() -> None:
    with pytest.raises(InvalidInputError) as info:
        MATRIX.replace({"user": frozenset({"unknown.perm"})})
    assert info.value.code == "roles.unknown_permission"
    with pytest.raises(InvalidInputError):
        MATRIX.replace({"Bad Name": frozenset()})
    updated = MATRIX.replace({**MATRIX.roles, "helper": frozenset({"users.view"})})
    assert updated.effective_perms({"helper"}) == {"users.view"}


def test_link_unlink_and_delete() -> None:
    user = _user()
    user.pull_events()
    user.link_provider(Provider.YANDEX)
    user.unlink_provider(Provider.YANDEX)
    user.delete(NOW)
    linked, unlinked, deleted = user.pull_events()
    assert isinstance(linked, ExternalAccountLinked)
    assert linked.payload() == {"user_id": str(user.id), "provider": "yandex", "version": 2}
    assert isinstance(unlinked, ExternalAccountUnlinked)
    assert isinstance(deleted, UserDeleted)
    assert deleted.payload()["deleted_at"] == NOW.isoformat()
    assert user.status is UserStatus.DELETED
    # Ник и e-mail освобождены: заменены значениями, недоступными при регистрации.
    assert user.nickname.value.startswith("~deleted~")
    assert user.email.value.endswith("@deleted.invalid")
    with pytest.raises(DomainError):
        user.delete(NOW)
    with pytest.raises(DomainError):
        user.link_provider(Provider.YANDEX)


# ----------------------------------------------------------------- refresh rotation


def _session() -> tuple[Session, RefreshToken]:
    session = Session(id=uuid.uuid4(), user_id=uuid.uuid4(), kind=SessionKind.WEB, created_at=NOW)
    token = RefreshToken(
        id=uuid.uuid4(),
        session_id=session.id,
        token_hash="h1",
        parent_id=None,
        created_at=NOW,
        expires_at=NOW + timedelta(days=30),
    )
    return session, token


def test_rotation_issues_child_and_marks_parent_used() -> None:
    session, token = _session()
    later = NOW + timedelta(minutes=20)
    result = rotate_refresh_token(
        session, token, now=later, new_id=uuid.uuid4(), new_hash="h2", ttl=timedelta(days=30)
    )
    assert isinstance(result, Rotated)
    assert token.used_at == later
    assert result.token.parent_id == token.id
    assert result.token.expires_at == later + timedelta(days=30)
    assert session.last_seen_at == later


def test_reuse_revokes_whole_session() -> None:
    session, token = _session()
    rotate_refresh_token(
        session, token, now=NOW, new_id=uuid.uuid4(), new_hash="h2", ttl=timedelta(days=1)
    )
    result = rotate_refresh_token(
        session, token, now=NOW, new_id=uuid.uuid4(), new_hash="h3", ttl=timedelta(days=1)
    )
    assert isinstance(result, ReuseDetected)
    assert session.revoked_at == NOW
    assert session.revoke_reason == "refresh_reuse"
    assert not session.is_active


def test_expired_or_revoked_cannot_rotate() -> None:
    session, token = _session()
    with pytest.raises(UnauthenticatedError) as info:
        rotate_refresh_token(
            session,
            token,
            now=NOW + timedelta(days=31),
            new_id=uuid.uuid4(),
            new_hash="h2",
            ttl=timedelta(days=30),
        )
    assert info.value.code == "auth.refresh_expired"
    session.revoke(NOW, "logout")
    with pytest.raises(UnauthenticatedError) as info:
        rotate_refresh_token(
            session, token, now=NOW, new_id=uuid.uuid4(), new_hash="h2", ttl=timedelta(days=30)
        )
    assert info.value.code == "auth.session_revoked"
    assert not session.revoke(NOW, "again")  # повторный отзыв — no-op
