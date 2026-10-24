"""Домен друзей: пара, код, решение по заявке, видимость (ADR 0050)."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st
from social_service.domain.config import FriendsConfig
from social_service.domain.config_rules import semantic_errors
from social_service.domain.friends import (
    FRIEND_CODE_ALPHABET,
    FRIEND_CODE_LENGTH,
    AlreadyFriendsError,
    BlockedByYouError,
    DailyRequestsLimitError,
    FriendsLimitError,
    PlayerNotFoundError,
    Privacy,
    RequestAction,
    RequestFacts,
    RequestPendingError,
    decide_request,
    generate_friend_code,
    ordered_pair,
    relation_of,
)

from gv_testkit import FakeRandom

pytestmark = pytest.mark.unit

A, B = UUID(int=1), UUID(int=2)
CFG = FriendsConfig(
    code_attempts_per_hour=30, invites_per_minute=10, max_friends=3, requests_per_day=2
)
OK = RequestFacts(
    target_exists=True,
    sender_blocked_target=False,
    target_blocked_sender=False,
    target_policy="all",
    already_friends=False,
    outgoing_pending=False,
    incoming_pending=False,
    sent_in_window=0,
    sender_friends=0,
    target_friends=0,
)


@given(st.uuids(), st.uuids())
def test_pair_is_ordered_and_symmetric(a: UUID, b: UUID) -> None:
    low, high = ordered_pair(a, b)
    assert low.int <= high.int
    assert ordered_pair(b, a) == (low, high)


@given(st.integers(0, 10_000))
def test_friend_code_has_room_code_alphabet_and_is_deterministic_by_seed(seed: int) -> None:
    code = generate_friend_code(FakeRandom(seed))
    assert len(code) == FRIEND_CODE_LENGTH == 8
    assert set(code) <= set(FRIEND_CODE_ALPHABET)
    assert not set(code) & set("0O1I")
    assert code == generate_friend_code(FakeRandom(seed))


def test_plain_request_is_created() -> None:
    assert decide_request(A, B, OK, CFG) is RequestAction.CREATE


@pytest.mark.parametrize(
    "facts",
    [
        {"target_exists": False},
        {"target_blocked_sender": True},
        {"target_policy": "none"},
    ],
)
def test_unknown_blocking_and_closed_targets_look_the_same(facts: dict[str, object]) -> None:
    with pytest.raises(PlayerNotFoundError) as raised:
        decide_request(A, B, replace(OK, **facts), CFG)
    assert raised.value.code == "social.player_not_found"


def test_self_request_looks_like_unknown_player() -> None:
    with pytest.raises(PlayerNotFoundError):
        decide_request(A, A, OK, CFG)


def test_blocked_by_sender_is_reported_to_sender() -> None:
    with pytest.raises(BlockedByYouError):
        decide_request(A, B, replace(OK, sender_blocked_target=True), CFG)


def test_target_block_wins_over_sender_block_to_avoid_leaking() -> None:
    both = replace(OK, sender_blocked_target=True, target_blocked_sender=True)
    with pytest.raises(PlayerNotFoundError):
        decide_request(A, B, both, CFG)


def test_friends_and_pending_conflicts() -> None:
    with pytest.raises(AlreadyFriendsError):
        decide_request(A, B, replace(OK, already_friends=True), CFG)
    with pytest.raises(RequestPendingError):
        decide_request(A, B, replace(OK, outgoing_pending=True), CFG)


def test_counter_request_merges_even_if_target_hides_requests() -> None:
    facts = replace(OK, incoming_pending=True, target_policy="none", sent_in_window=99)
    assert decide_request(A, B, facts, CFG) is RequestAction.MERGE


def test_merge_respects_friends_limit_on_both_sides() -> None:
    for side in ("sender_friends", "target_friends"):
        facts = replace(OK, incoming_pending=True, **{side: CFG.max_friends})
        with pytest.raises(FriendsLimitError):
            decide_request(A, B, facts, CFG)


def test_limits_come_from_config() -> None:
    with pytest.raises(FriendsLimitError):
        decide_request(A, B, replace(OK, sender_friends=3), CFG)
    with pytest.raises(DailyRequestsLimitError):
        decide_request(A, B, replace(OK, sent_in_window=2), CFG)
    loose = replace(CFG, requests_per_day=100, max_friends=100)
    assert decide_request(A, B, replace(OK, sent_in_window=50, sender_friends=50), loose) is (
        RequestAction.CREATE
    )


def test_daily_limit_retry_after_is_the_whole_day_window() -> None:
    with pytest.raises(DailyRequestsLimitError) as err:
        decide_request(A, B, replace(OK, sent_in_window=2), CFG)
    assert err.value.details == {"retry_after": 86400}


@given(sent=st.integers(0, 50), limit=st.integers(1, 50))
def test_daily_limit_is_monotonic(sent: int, limit: int) -> None:
    cfg = replace(CFG, requests_per_day=limit)
    allowed = True
    try:
        decide_request(A, B, replace(OK, sent_in_window=sent), cfg)
    except DailyRequestsLimitError:
        allowed = False
    assert allowed == (sent < limit)


def view(**kwargs: object) -> dict[str, bool]:
    base = {
        "viewer": A,
        "target": B,
        "target_exists": True,
        "privacy": Privacy(),
        "is_friend": False,
        "viewer_blocked_target": False,
        "target_blocked_viewer": False,
        "outgoing_pending": False,
    }
    result = relation_of(**{**base, **kwargs})  # type: ignore[arg-type]  # тестовый словарь
    return {
        "profile": result.profile_visible,
        "stats": result.stats_visible,
        "friend": result.is_friend,
        "can": result.can_send_request,
        "blocked_by_me": result.blocked_by_me,
    }


def test_relation_hidden_unknown_and_blocking_are_identical() -> None:
    empty = view(target_exists=False)
    assert empty == {
        "profile": False,
        "stats": False,
        "friend": False,
        "can": False,
        "blocked_by_me": False,
    }
    assert view(privacy=Privacy(profile_public=False)) == empty
    assert view(target_blocked_viewer=True) == empty


def test_relation_visible_rules() -> None:
    assert view()["can"] is True
    assert view(privacy=Privacy(friend_requests="none"))["can"] is False
    assert view(outgoing_pending=True)["can"] is False
    assert view(privacy=Privacy(stats_public=False)) | {"x": 0} == {
        "profile": True,
        "stats": False,
        "friend": False,
        "can": True,
        "blocked_by_me": False,
        "x": 0,
    }
    friend = view(is_friend=True, privacy=Privacy(profile_public=False, stats_public=False))
    assert (friend["profile"], friend["stats"], friend["friend"], friend["can"]) == (
        True,
        True,
        True,
        False,
    )
    assert view(viewer_blocked_target=True)["blocked_by_me"] is True
    assert view(viewer=B)["profile"] is True  # себя видно целиком


def base_body() -> dict[str, object]:
    return {
        "presence": {"heartbeat_s": 30, "ttl_s": 75},
        "sanctions": {"min_duration_s": 60, "max_duration_s": 120},
        "reports": {"reasons": [{"code": "a"}, {"code": "b"}]},
    }


def test_config_semantic_rules() -> None:
    assert semantic_errors(base_body()) == []
    body = base_body()
    body["presence"] = {"heartbeat_s": 30, "ttl_s": 30}
    body["sanctions"] = {"min_duration_s": 200, "max_duration_s": 120}
    body["reports"] = {"reasons": [{"code": "a"}, {"code": "a"}]}
    assert {e["path"] for e in semantic_errors(body)} == {
        "presence.ttl_s",
        "sanctions.min_duration_s",
        "reports.reasons",
    }
