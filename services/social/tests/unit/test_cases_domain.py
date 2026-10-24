"""Правила переходов кейса (property-based): версия монотонна, решённый кейс не меняется."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from social_service.domain import cases as rules
from social_service.domain.cases import ModerationCase

from gv_core.errors import ConflictError, ForbiddenError, InvalidInputError

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 24, tzinfo=UTC)
USERS = [UUID(int=i) for i in range(1, 4)]
PERMS = [frozenset(), frozenset({rules.SENIOR_PERM})]
FRESH = ModerationCase(
    UUID(int=9), "comment", UUID(int=8), UUID(int=7), rules.OPEN, None, NOW, version=1
)

actions = st.tuples(
    st.sampled_from(["claim", "escalate", "resolve"]),
    st.sampled_from(USERS),
    st.sampled_from(PERMS),
    st.booleans(),  # актуальная версия или устаревшая
    st.sampled_from([rules.DISMISSED, rules.CONTENT_REMOVED, rules.SANCTIONED]),
)


@given(st.lists(actions, max_size=25))
def test_version_is_monotonic_and_resolved_is_final(steps: list[tuple[object, ...]]) -> None:
    case = FRESH
    for name, actor, perms, fresh, decision in steps:  # type: ignore[misc]
        version = case.version if fresh else case.version + 5
        before = case
        try:
            if name == "claim":
                case = rules.claim(case, actor, version)
            elif name == "escalate":
                case = rules.escalate(case, actor, version)
            else:
                case = rules.resolve(case, actor, perms, version, decision, NOW)
        except (ConflictError, ForbiddenError, InvalidInputError):
            assert case == before  # отказ ничего не меняет
            continue
        assert case.version >= before.version
        assert before.status != rules.RESOLVED  # из решённого переходов нет
        if case.status == rules.RESOLVED:
            assert case.decision == decision and case.resolved_by == actor


@given(st.sampled_from(USERS), st.sampled_from(PERMS))
def test_only_senior_resolves_escalated(actor: UUID, perms: frozenset[str]) -> None:
    escalated = rules.escalate(rules.claim(FRESH, USERS[0], 1), USERS[0], 2)
    if rules.is_senior(perms):
        assert rules.resolve(escalated, actor, perms, 3, rules.DISMISSED, NOW).status == (
            rules.RESOLVED
        )
    else:
        with pytest.raises(ForbiddenError):
            rules.resolve(escalated, actor, perms, 3, rules.DISMISSED, NOW)
