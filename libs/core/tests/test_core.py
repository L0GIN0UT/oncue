from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

import pytest
from hypothesis import given
from hypothesis import strategies as st

from gv_core import AggregateRoot, DomainEvent, InvalidInputError, RequestContext
from gv_core.context import bind_context, current_context
from gv_core.ids import uuid7, uuid7_timestamp_ms
from gv_core.pagination import clamp_limit, decode_cursor, encode_cursor

pytestmark = pytest.mark.unit


class TestUuid7:
    def test_version_and_variant(self) -> None:
        value = uuid7()
        assert value.version == 7
        assert value.variant == uuid.RFC_4122

    def test_strictly_increasing_within_process(self) -> None:
        values = [uuid7() for _ in range(5000)]
        assert values == sorted(values)
        assert len(set(values)) == len(values)

    def test_monotonic_even_if_clock_goes_back(self) -> None:
        first = uuid7(timestamp_ms=2_000_000_000_000)
        second = uuid7(timestamp_ms=1_000_000_000_000)
        assert second > first

    def test_timestamp_roundtrip(self) -> None:
        ts = 4_000_000_000_000
        assert uuid7_timestamp_ms(uuid7(timestamp_ms=ts)) >= ts

    def test_timestamp_rejects_other_versions(self) -> None:
        with pytest.raises(ValueError, match="v4"):
            uuid7_timestamp_ms(uuid.uuid4())


class Color(Enum):
    RED = "red"


@dataclass(frozen=True, kw_only=True)
class Thing:
    n: int


@dataclass(frozen=True, kw_only=True)
class SomethingHappened(DomainEvent):
    aggregate_type = "thing"
    who: uuid.UUID
    at: datetime
    color: Color
    tags: tuple[str, ...]
    nested: Thing


class Counter(AggregateRoot[uuid.UUID]):
    def __init__(self, id: uuid.UUID) -> None:  # noqa: A002
        super().__init__(id)
        self.value = 0

    def increment(self) -> None:
        self.value += 1
        self.record(
            SomethingHappened(
                aggregate_id=str(self.id),
                who=self.id,
                at=datetime(2026, 1, 1, tzinfo=UTC),
                color=Color.RED,
                tags=("a",),
                nested=Thing(n=self.value),
            )
        )


class TestDomain:
    def test_events_are_collected_and_pulled_once(self) -> None:
        counter = Counter(uuid7())
        counter.increment()
        counter.increment()
        assert len(counter.pending_events) == 2
        assert len(counter.pull_events()) == 2
        assert counter.pull_events() == []

    def test_payload_is_json_primitive(self) -> None:
        counter = Counter(uuid7())
        counter.increment()
        (event,) = counter.pull_events()
        assert event.event_name() == "SomethingHappened"
        assert event.payload() == {
            "who": str(counter.id),
            "at": "2026-01-01T00:00:00+00:00",
            "color": "red",
            "tags": ["a"],
            "nested": {"n": 1},
        }

    def test_entity_equality_by_id(self) -> None:
        same = uuid7()
        assert Counter(same) == Counter(same)
        assert Counter(same) != Counter(uuid7())
        assert len({Counter(same), Counter(same)}) == 1


class TestContext:
    def test_bind_and_restore(self) -> None:
        assert current_context() is None
        ctx = RequestContext(correlation_id=uuid7())
        with bind_context(ctx):
            assert current_context() == ctx
        assert current_context() is None


class TestPagination:
    @given(st.dictionaries(st.text(max_size=10), st.integers() | st.text(max_size=20), max_size=5))
    def test_cursor_roundtrip(self, position: dict[str, object]) -> None:
        cursor = encode_cursor(position)
        if len(cursor) <= 1024:
            assert decode_cursor(cursor) == position

    @pytest.mark.parametrize("bad", ["!!!", "bm90LWpzb24", "WzFd", "a" * 2000])
    def test_bad_cursor_is_input_error(self, bad: str) -> None:
        with pytest.raises(InvalidInputError) as info:
            decode_cursor(bad)
        assert info.value.code == "pagination.bad_cursor"

    @pytest.mark.parametrize(
        ("given_limit", "expected"), [(None, 20), (0, 1), (-5, 1), (50, 50), (10_000, 100)]
    )
    def test_clamp_limit(self, given_limit: int | None, expected: int) -> None:
        assert clamp_limit(given_limit) == expected
