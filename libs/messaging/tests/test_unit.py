from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

import pytest

from gv_core import DomainEvent, RequestContext, bind_context, uuid7
from gv_messaging.envelope import EventEnvelope, dlq_topic, topic_name
from gv_messaging.ports import PermanentError
from gv_messaging.rabbit import DeadLetter, RetryStep, TaskMessage, next_step
from gv_messaging.retry import RetryPolicy
from gv_testkit import FakeClock

pytestmark = pytest.mark.unit


@dataclass(frozen=True, kw_only=True)
class RoundFinished(DomainEvent):
    aggregate_type = "game"
    event_version = 2
    score: int


class TestEnvelope:
    def test_from_domain_event_takes_context(self, fake_clock: FakeClock) -> None:
        correlation, causation = uuid7(), uuid7()
        with bind_context(RequestContext(correlation_id=correlation, causation_id=causation)):
            env = EventEnvelope.from_domain_event(
                RoundFinished(aggregate_id="g1", score=6), producer="game", clock=fake_clock
            )
        assert env.event_type == "game.RoundFinished"
        assert env.event_version == 2
        assert env.topic == "game.game.v2"
        assert env.correlation_id == correlation
        assert env.causation_id == causation
        assert env.payload == {"score": 6}
        assert env.occurred_at == fake_clock.now()

    def test_roundtrip_bytes(self, fake_clock: FakeClock) -> None:
        env = EventEnvelope.from_domain_event(
            RoundFinished(aggregate_id="g1", score=1), producer="game", clock=fake_clock
        )
        assert EventEnvelope.from_bytes(env.to_bytes()) == env

    def test_unknown_fields_are_ignored(self, fake_clock: FakeClock) -> None:
        env = EventEnvelope.from_domain_event(
            RoundFinished(aggregate_id="g1", score=1), producer="game", clock=fake_clock
        )
        data = env.model_dump(mode="json") | {"new_field_from_future": 1}
        assert EventEnvelope.model_validate(data) == env

    @pytest.mark.parametrize("bad", ["Game", "game.x", "1game", ""])
    def test_topic_name_validation(self, bad: str) -> None:
        with pytest.raises(ValueError, match="недопустимое"):
            topic_name(bad, "agg", 1)

    def test_dlq_topic(self) -> None:
        assert dlq_topic("rating.consumer") == "rating.consumer.dlq"


class TestRetryPolicy:
    def test_delay_is_bounded_and_grows(self) -> None:
        policy = RetryPolicy(max_attempts=5, base_delay=1, factor=2, max_delay=5)
        rng = random.Random(1)
        for attempt, ceiling in [(1, 1), (2, 2), (3, 4), (4, 5), (5, 5)]:
            for _ in range(50):
                assert 0 <= policy.delay(attempt, rng) <= ceiling

    def test_should_retry(self) -> None:
        policy = RetryPolicy(max_attempts=3)
        assert [policy.should_retry(a) for a in (1, 2, 3)] == [True, True, False]

    @pytest.mark.parametrize("kwargs", [{"max_attempts": 0}, {"factor": 0.5}, {"base_delay": -1}])
    def test_invalid_policy(self, kwargs: dict[str, Any]) -> None:
        with pytest.raises(ValueError):
            RetryPolicy(**kwargs)


class TestTaskRetrySteps:
    delays = (1.0, 10.0, 60.0)

    def test_transient_errors_walk_delay_queues_then_dlq(self) -> None:
        err = RuntimeError("timeout")
        assert next_step(1, err, self.delays) == RetryStep(0)
        assert next_step(3, err, self.delays) == RetryStep(2)
        assert next_step(4, err, self.delays) == DeadLetter("retries_exhausted")

    def test_permanent_error_goes_straight_to_dlq(self) -> None:
        assert next_step(1, PermanentError("bad"), self.delays) == DeadLetter("permanent")

    def test_task_message_roundtrip_and_bad_payload(self) -> None:
        msg = TaskMessage("t1", "generate_assets", {"item": "x"}, attempt=2)
        assert TaskMessage.from_bytes(msg.to_bytes()) == msg
        with pytest.raises(PermanentError):
            TaskMessage.from_bytes(b"{not json")


async def test_kafka_consumer_accepts_any_processor_port() -> None:
    """KafkaEventConsumer зависит от EventProcessorPort, а не от EventProcessor."""
    from gv_messaging import EventProcessorPort, Outcome
    from gv_messaging.kafka import KafkaEventConsumer

    class CustomProcessor:
        async def process(self, raw: bytes, *, source: str) -> Outcome:
            return Outcome.PROCESSED

    processor: EventProcessorPort = CustomProcessor()
    consumer = KafkaEventConsumer(
        bootstrap_servers="localhost:9092", group="g", topics=["t"], processor=processor
    )
    assert consumer._processor is processor
