"""Правила комментариев и жалоб: окно правки, права удаления, порог кейса (чистый домен)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from social_service.domain import discussion as rules
from social_service.domain.discussion import Comment

pytestmark = pytest.mark.unit

AUTHOR = UUID(int=1)
OWNER = UUID(int=2)
OTHER = UUID(int=3)
T0 = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def _comment(**kw: object) -> Comment:
    base = {"id": UUID(int=9), "collection_id": UUID(int=8), "author_id": AUTHOR, "body": "x"}
    return Comment(**{**base, "created_at": T0, **kw})  # type: ignore[arg-type]  # тестовая сборка


def test_author_edits_inside_window_only() -> None:
    comment = _comment()
    assert rules.can_edit(comment, AUTHOR, T0 + timedelta(seconds=300), 300)
    assert not rules.can_edit(comment, AUTHOR, T0 + timedelta(seconds=301), 300)


def test_only_author_edits_and_deleted_is_frozen() -> None:
    assert not rules.can_edit(_comment(), OTHER, T0, 300)
    assert not rules.can_edit(_comment(deleted_at=T0), AUTHOR, T0, 300)


@given(a=st.integers(0, 10_000), b=st.integers(0, 10_000), window=st.integers(1, 3_600))
def test_edit_window_is_monotonic(a: int, b: int, window: int) -> None:
    """Если править уже нельзя, позже тем более нельзя."""
    early, late = sorted((a, b))
    comment = _comment()
    if not rules.can_edit(comment, AUTHOR, T0 + timedelta(seconds=early), window):
        assert not rules.can_edit(comment, AUTHOR, T0 + timedelta(seconds=late), window)


@pytest.mark.parametrize(
    ("actor", "perms", "owner", "expected"),
    [
        (AUTHOR, frozenset(), OWNER, rules.ROLE_AUTHOR),
        (OWNER, frozenset(), OWNER, rules.ROLE_COLLECTION_OWNER),
        (OTHER, frozenset({rules.DELETE_ANY_PERM}), OWNER, rules.ROLE_MODERATOR),
        (AUTHOR, frozenset({rules.DELETE_ANY_PERM}), OWNER, rules.ROLE_AUTHOR),
        (OTHER, frozenset(), OWNER, None),
        (OTHER, frozenset(), None, None),
    ],
)
def test_delete_role(actor: UUID, perms: frozenset[str], owner: UUID | None, expected: str) -> None:
    assert rules.delete_role(_comment(), actor, perms, owner) == expected


@pytest.mark.parametrize(("count", "expected"), [(2, False), (3, True), (4, True)])
def test_threshold(count: int, expected: bool) -> None:
    assert rules.threshold_reached(count, 3) is expected


@pytest.mark.parametrize(
    ("finish", "field"),
    [
        ("forfeit", "forfeits"),
        ("left", "leaves"),
        ("tech_loss", "tech_losses"),
        ("completed", None),
        ("tech_win", None),
    ],
)
def test_counter_for_finish(finish: str, field: str | None) -> None:
    assert rules.counter_for_finish(finish) == field


def test_localized_prefers_language_then_ru_then_any() -> None:
    titles = {"ru": "Привет", "en": "Hi"}
    assert rules.localized(titles, "en") == "Hi"
    assert rules.localized(titles, "de") == "Привет"
    assert rules.localized({"en": "Hi"}, "de") == "Hi"
