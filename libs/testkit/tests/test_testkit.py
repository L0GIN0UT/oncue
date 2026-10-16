"""Тесты самого testkit: фейки должны вести себя как реальные реализации портов,
иначе тесты сервисов на них будут проверять не то."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
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


_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents if (p / "contracts" / "events").is_dir()
)
_EXAMPLES = sorted((_REPO_ROOT / "contracts" / "events").glob("*/examples/*.v*.json"))


class TestContractExamples:
    def test_examples_have_unique_event_ids(self) -> None:
        import json

        ids = [json.loads(p.read_text(encoding="utf-8"))["event_id"] for p in _EXAMPLES]
        assert len(_EXAMPLES) > 1
        duplicates = {i for i in ids if ids.count(i) > 1}
        assert not duplicates, f"повторяются event_id в примерах: {sorted(duplicates)}"

    def test_example_event_ids_are_uuid7(self) -> None:
        import json

        for path in _EXAMPLES:
            event_id = UUID(json.loads(path.read_text(encoding="utf-8"))["event_id"])
            assert event_id.version == 7, path

    @pytest.fixture
    def contracts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GV_CONTRACTS_DIR", str(_REPO_ROOT / "contracts"))

    @pytest.mark.usefixtures("contracts")
    def test_load_example_gives_fresh_uuid7_each_call(self) -> None:
        from gv_testkit import assert_event_matches_schema, load_example

        first = load_example("catalog.MediaItemCreated")
        second = load_example("catalog.MediaItemCreated")
        assert first.event_id != second.event_id
        assert first.event_id.version == 7
        assert first.payload == second.payload
        assert_event_matches_schema(first)

    @pytest.mark.usefixtures("contracts")
    def test_load_example_can_keep_file_event_id(self) -> None:
        import json

        from gv_testkit import load_example

        path = _REPO_ROOT / "contracts/events/catalog/examples/MediaItemCreated.v1.json"
        stored = UUID(json.loads(path.read_text(encoding="utf-8"))["event_id"])
        envelope = load_example("catalog.MediaItemCreated", fresh_id=False)
        assert envelope.event_id == stored
        assert load_example("catalog.MediaItemCreated", fresh_id=False) == envelope

    @pytest.mark.usefixtures("contracts")
    def test_load_example_variant(self) -> None:
        """Вариант примера той же схемы: ``<Event>.<variant>.v<N>.json``."""
        from gv_testkit import assert_event_matches_schema, load_example

        envelope = load_example("game.GameFinished", variant="collection")
        assert envelope.payload["collection_id"] is not None
        assert load_example("game.GameFinished").payload.get("collection_id") is None
        assert_event_matches_schema(envelope)

    @pytest.mark.usefixtures("contracts")
    def test_every_example_file_matches_its_schema(self) -> None:
        """Все примеры, включая варианты, проходят схему своего события."""
        from gv_testkit import assert_event_matches_schema, load_example

        for path in _EXAMPLES:
            event, *variant, version = path.name.removesuffix(".json").split(".")
            envelope = load_example(
                f"{path.parent.parent.name}.{event}",
                int(version.removeprefix("v")),
                variant=".".join(variant) or None,
            )
            assert envelope.event_type == f"{path.parent.parent.name}.{event}", path
            assert_event_matches_schema(envelope)


class TestInMemoryIdempotencyStore:
    """Фейк ведёт себя как ``SqlIdempotencyStore`` для ``run_idempotent``."""

    async def test_replay_and_conflicts(self) -> None:
        from gv_core import ConflictError
        from gv_db.idempotency import run_idempotent
        from gv_testkit import InMemoryIdempotencyStore

        store = InMemoryIdempotencyStore()
        actor = uuid7()
        calls: list[int] = []

        async def perform() -> dict[str, Any]:
            calls.append(1)
            return {"n": len(calls)}

        assert await run_idempotent(store, actor, "key-mem-01", "a" * 64, perform) == (
            {"n": 1},
            True,
        )
        assert await run_idempotent(store, actor, "key-mem-01", "a" * 64, perform) == (
            {"n": 1},
            False,
        )
        assert calls == [1]
        with pytest.raises(ConflictError) as reused:
            await run_idempotent(store, actor, "key-mem-01", "b" * 64, perform)
        assert reused.value.code == "idempotency.key_reused"
        with pytest.raises(ConflictError) as busy:
            await store.save(actor, "key-mem-01", "a" * 64, {})
        assert busy.value.code == "idempotency.in_progress"
        # Другой актор — независимый ключ.
        assert (await run_idempotent(store, uuid7(), "key-mem-01", "a" * 64, perform))[1]


async def test_issue_service_token_claims(token_issuer: TestTokenIssuer) -> None:
    import jwt as pyjwt

    token = token_issuer.issue_service("game", "media_pipeline", ["assets.link", "x"])
    claims = pyjwt.decode(token, options={"verify_signature": False})
    assert claims["sub"] == "svc:game"
    assert claims["aud"] == "media_pipeline"
    assert claims["scope"] == "assets.link x"
    assert claims["exp"] - claims["iat"] == 600
    assert not {"sid", "roles", "perms"} & claims.keys()


async def test_issue_guest_token(token_issuer: TestTokenIssuer) -> None:
    principal = await token_issuer.verifier().verify(token_issuer.issue_guest())
    assert principal.is_guest
    assert principal.perms == frozenset()
    assert principal.session_id


def test_service_token_fixture(service_token: Any) -> None:
    assert service_token("admin", "identity", ["admin_sessions"]).count(".") == 2


def test_fake_random_is_deterministic_and_scriptable() -> None:
    from gv_testkit import FakeRandom

    a, b = FakeRandom(seed=7), FakeRandom(seed=7)
    assert a.sample(range(50), 5) == b.sample(range(50), 5)
    assert a.randrange(10) == b.randrange(10)
    items_a, items_b = list(range(10)), list(range(10))
    a.shuffle(items_a)
    b.shuffle(items_b)
    assert items_a == items_b
    scripted = FakeRandom(values=[0.0, 0.5])
    assert [scripted.random() for _ in range(3)] == [0.0, 0.5, 0.0]


@pytest.mark.integration
async def test_assert_idempotency_purge_on_demo_schema(database_url: str) -> None:
    from gv_db.idempotency import idempotency_table
    from gv_db.metadata import create_metadata
    from gv_service import Infrastructure, ServiceDefinition, ServiceSettings
    from gv_testkit import FakeClock, assert_idempotency_purge, create_schema

    tables = create_metadata()
    idempotency_table(tables.metadata)
    await create_schema(database_url, tables.metadata)
    from pydantic import SecretStr

    settings = ServiceSettings(service_name="demo", database_url=SecretStr(database_url))
    infra = await Infrastructure.open(settings, tables, clock=FakeClock())
    try:
        definition = ServiceDefinition(name="demo", settings_cls=ServiceSettings, tables=tables)
        await assert_idempotency_purge(definition, infra)
    finally:
        await infra.close()


class TestS3WithoutDocker:
    """Без ``GV_TEST_S3_ENDPOINT`` S3 поднимается из ``moto_server`` — реестр образов не нужен."""

    pytestmark = pytest.mark.integration

    def test_starts_moto_and_stops_it_on_close(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import boto3

        from gv_testkit import InfraProvider

        monkeypatch.delenv("GV_TEST_S3_ENDPOINT", raising=False)
        provider = InfraProvider()
        try:
            endpoint = provider.s3_endpoint()
            assert provider.s3_endpoint() == endpoint  # один процесс на сессию
            s3 = boto3.client(
                "s3",
                endpoint_url=endpoint.url,
                aws_access_key_id=endpoint.access_key,
                aws_secret_access_key=endpoint.secret_key,
                region_name="us-east-1",
            )
            s3.create_bucket(Bucket="probe")
            s3.put_object(Bucket="probe", Key="k", Body=b"v")
            assert s3.get_object(Bucket="probe", Key="k")["Body"].read() == b"v"
        finally:
            provider.close()
        import urllib.request

        with pytest.raises(OSError):
            urllib.request.urlopen(endpoint.url, timeout=1)  # noqa: S310 — локальный адрес
