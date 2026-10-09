"""Тесты самого testkit: фейки должны вести себя как реальные реализации портов,
иначе тесты сервисов на них будут проверять не то."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

import gv_testkit
from gv_core import AggregateRoot, DomainEvent, UnauthenticatedError, uuid7
from gv_messaging import EventEnvelope
from gv_testkit import (
    FakeClock,
    FakeEventPublisher,
    FakeUnitOfWork,
    TestTokenIssuer,
    assert_published,
    published_envelopes,
)

pytestmark = pytest.mark.unit


@dataclass(frozen=True, kw_only=True)
class Pinged(DomainEvent):
    aggregate_type = "probe"
    n: int


class Probe(AggregateRoot[UUID]):
    def ping(self, n: int) -> None:
        self.record(Pinged(aggregate_id=str(self.id), n=n))


def test_lazy_exports_match_all() -> None:
    """``__all__`` и ленивая таблица экспорта не должны расходиться."""
    assert sorted(gv_testkit.__all__) == sorted(gv_testkit._EXPORTS)
    for name in gv_testkit.__all__:
        assert getattr(gv_testkit, name) is not None
    with pytest.raises(AttributeError):
        _ = gv_testkit.does_not_exist  # type: ignore[attr-defined]


class TestFakeClock:
    def test_is_frozen_and_advances(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        clock = FakeClock(start)
        assert clock.now() == clock.now() == start
        clock.advance(minutes=5)
        assert clock.now() == start + timedelta(minutes=5)

    def test_returns_aware_utc(self) -> None:
        assert FakeClock().now().tzinfo is not None


class TestFakeUnitOfWork:
    async def test_commit_collects_events(self) -> None:
        uow, probe = FakeUnitOfWork(), Probe(uuid7())
        async with uow:
            uow.track(probe)
            probe.ping(1)
            await uow.commit()
        assert [e.payload() for e in uow.events] == [{"n": 1}]
        assert uow.committed and not probe.pending_events

    async def test_without_commit_rolls_back_and_drops_events(self) -> None:
        uow, probe = FakeUnitOfWork(), Probe(uuid7())
        async with uow:
            uow.track(probe)
            probe.ping(1)
        assert uow.rolled_back and uow.events == [] and not probe.pending_events


class TestEvents:
    async def test_assert_published_finds_exactly_one(self) -> None:
        clock, publisher = FakeClock(), FakeEventPublisher()
        for n in (1, 2):
            env = EventEnvelope.from_domain_event(
                Pinged(aggregate_id="a", n=n), producer="probe", clock=clock
            )
            await publisher.publish("probe.probe.v1", key=env.aggregate_id, value=env.to_bytes())
        envelopes = published_envelopes(publisher)
        assert assert_published(envelopes, "probe.Pinged", where={"n": 2}).payload == {"n": 2}
        with pytest.raises(AssertionError, match="ровно одно"):
            assert_published(envelopes, "probe.Pinged")


class TestTokenIssuerContract:
    async def test_issued_token_is_accepted_by_its_verifier(self) -> None:
        issuer = TestTokenIssuer()
        user = uuid7()
        principal = await issuer.verifier().verify(
            issuer.issue(user_id=user, roles=["moderator"], perms=["comments.delete_any"])
        )
        assert principal.user_id == user
        assert principal.has_perm("comments.delete_any")

    async def test_foreign_issuer_is_rejected(self) -> None:
        token = TestTokenIssuer(kid="other").issue()
        with pytest.raises(UnauthenticatedError):
            await TestTokenIssuer().verifier().verify(token)

    async def test_expired_token_is_rejected(self) -> None:
        issuer = TestTokenIssuer()
        token = issuer.issue(now=datetime.now(UTC) - timedelta(hours=1), ttl=timedelta(minutes=1))
        with pytest.raises(UnauthenticatedError):
            await issuer.verifier().verify(token)


class TestS3Endpoint:
    def test_parses_credentials_from_url(self) -> None:
        from gv_testkit import S3Endpoint

        endpoint = S3Endpoint.parse("http://key:secret@127.0.0.1:9000")
        assert (endpoint.url, endpoint.access_key, endpoint.secret_key) == (
            "http://127.0.0.1:9000",
            "key",
            "secret",
        )
        with pytest.raises(ValueError):
            S3Endpoint.parse("http://127.0.0.1:9000")


class TestStubs:
    async def test_stub_answers_and_checks_contract(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        import json

        import httpx

        from gv_testkit import stub_service

        (tmp_path / "events").mkdir()
        (tmp_path / "openapi").mkdir()
        (tmp_path / "openapi" / "demo.json").write_text(
            json.dumps({"paths": {"/v1/items/{item_id}": {"get": {}}}})
        )
        monkeypatch.setenv("GV_CONTRACTS_DIR", str(tmp_path))
        with stub_service("http://demo:8000", service="demo") as demo:
            demo.route("GET", "/v1/items/42").respond(200, json={"id": 42})
            async with httpx.AsyncClient() as client:
                response = await client.get("http://demo:8000/v1/items/42")
            assert response.json() == {"id": 42}
            with pytest.raises(AssertionError, match=r"нет в contracts/openapi/demo\.json"):
                demo.route("POST", "/v1/items/42")
