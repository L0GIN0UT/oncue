"""bff -> collections/media-pipeline: обложки идут под сервисными токенами, сбои не страшны."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
import respx
from bff_web_service.application.dto import MediaLink
from bff_web_service.application.ports import UpstreamRejectedError, UpstreamUnavailableError
from bff_web_service.application.queries import CollectionCovers, GetCollectionCovers
from bff_web_service.infrastructure.clients import HttpCollectionsClient, HttpMediaClient
from bff_web_service.infrastructure.http import CircuitBreaker, UpstreamHttp

from gv_core.errors import DependencyUnavailableError

pytestmark = pytest.mark.unit

COLL = "http://collections.test"
MEDIA = "http://media.test"
C1 = UUID("0192a3b4-c5d6-7e8f-9a0b-000000000c01")
C2 = UUID("0192a3b4-c5d6-7e8f-9a0b-000000000c02")
M1 = UUID("0192a3b4-c5d6-7e8f-9a0b-0000000000a1")
M2 = UUID("0192a3b4-c5d6-7e8f-9a0b-0000000000a2")
LINK = MediaLink("https://m.test/a.webp", datetime(2026, 10, 16, 14, tzinfo=UTC), "image/webp")


class Tokens:
    def __init__(self, error: Exception | None = None) -> None:
        self.error, self.invalidated = error, 0

    async def token(self) -> str:
        if self.error is not None:
            raise self.error
        return "service-token"

    def invalidate(self) -> None:
        self.invalidated += 1


def upstream(service: str, base: str) -> UpstreamHttp:
    return UpstreamHttp(
        service,
        httpx.AsyncClient(base_url=base),
        CircuitBreaker(failures=5, reset_s=10),
        timeout_s=1.0,
    )


def collections(tokens: Tokens | None) -> HttpCollectionsClient:
    return HttpCollectionsClient(
        upstream("collections", COLL), catalog_ttl_s=30, cache_prefix="t:", service_tokens=tokens
    )


def media(tokens: Tokens | None) -> HttpMediaClient:
    return HttpMediaClient(upstream("media_pipeline", MEDIA), service_tokens=tokens)


@respx.mock
async def test_cover_items_post_under_service_token() -> None:
    route = respx.post(f"{COLL}/internal/v1/cover-items").respond(
        200, json={"items": [{"collection_id": str(C1), "media_item_id": str(M1)}]}
    )

    got = await collections(Tokens()).cover_items([C1, C2])

    assert got == {C1: M1}
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer service-token"
    assert request.method == "POST"


@respx.mock
async def test_cover_items_foreign_collection_is_contract_violation() -> None:
    respx.post(f"{COLL}/internal/v1/cover-items").respond(
        200, json={"items": [{"collection_id": str(C2), "media_item_id": str(M2)}]}
    )
    with pytest.raises(UpstreamUnavailableError):
        await collections(Tokens()).cover_items([C1])


@respx.mock
async def test_cover_items_unauthorized_drops_token_and_forbidden_is_rejection() -> None:
    respx.post(f"{COLL}/internal/v1/cover-items").respond(
        401, json={"code": "auth.unauthenticated"}
    )
    tokens = Tokens()
    with pytest.raises(UpstreamRejectedError):
        await collections(tokens).cover_items([C1])
    assert tokens.invalidated == 1

    respx.post(f"{COLL}/internal/v1/cover-items").respond(403, json={"code": "auth.forbidden"})
    with pytest.raises(UpstreamRejectedError) as caught:
        await collections(tokens).cover_items([C1])
    assert caught.value.status == 403
    assert tokens.invalidated == 1


async def test_cover_calls_without_service_auth_are_unavailable() -> None:
    with pytest.raises(UpstreamUnavailableError):
        await collections(None).cover_items([C1])
    with pytest.raises(UpstreamUnavailableError):
        await media(None).cover_links([M1])
    broken = Tokens(DependencyUnavailableError("identity"))
    with pytest.raises(UpstreamUnavailableError):
        await media(broken).cover_links([M1])


@respx.mock
async def test_cover_links_post_and_only_requested_units() -> None:
    link = {"media_item_id": str(M1), "url": LINK.url, "expires_at": "2026-10-16T14:00:00Z"}
    route = respx.post(f"{MEDIA}/internal/v1/cover-links").respond(
        200, json={"items": [{**link, "mime": "image/webp"}]}
    )

    got = await media(Tokens()).cover_links([M1, M2])

    assert got == {M1: LINK}
    assert route.calls.last.request.headers["authorization"] == "Bearer service-token"

    respx.post(f"{MEDIA}/internal/v1/cover-links").respond(
        200, json={"items": [{**link, "media_item_id": str(M2), "mime": "image/webp"}]}
    )
    with pytest.raises(UpstreamUnavailableError):
        await media(Tokens()).cover_links([M1])


# ----------------------------------------------------------------------------- CollectionCovers
@dataclass
class FakeCollections:
    answer: dict[UUID, UUID] | Exception = field(default_factory=dict)
    calls: list[list[UUID]] = field(default_factory=list)
    delay: float = 0

    async def cover_items(self, collection_ids: list[UUID]) -> dict[UUID, UUID]:
        self.calls.append(list(collection_ids))
        await asyncio.sleep(self.delay)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@dataclass
class FakeMedia:
    answer: dict[UUID, MediaLink] | Exception = field(default_factory=dict)
    calls: list[list[UUID]] = field(default_factory=list)

    async def cover_links(self, media_item_ids: list[UUID]) -> dict[UUID, MediaLink]:
        self.calls.append(list(media_item_ids))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def covers(coll: FakeCollections, med: FakeMedia | None, **kw: float) -> CollectionCovers:
    return CollectionCovers(
        coll,  # type: ignore[arg-type]  # фейк реализует нужный срез протокола
        med,  # type: ignore[arg-type]
        batch_max=int(kw.get("batch_max", 50)),
        deadline_s=kw.get("deadline_s", 1.0),
    )


async def test_resolve_makes_one_call_per_service_and_dedupes() -> None:
    coll, med = FakeCollections({C1: M1, C2: M1}), FakeMedia({M1: LINK})

    got = await covers(coll, med).resolve([C1, C2, C1])

    assert got == {C1: LINK, C2: LINK}
    assert coll.calls == [[C1, C2]]
    assert med.calls == [[M1]]  # общая картинка - одна ссылка в запросе


async def test_resolve_skips_media_when_nothing_to_show() -> None:
    coll, med = FakeCollections({}), FakeMedia()
    assert await covers(coll, med).resolve([C1]) == {}
    assert med.calls == []
    assert await covers(coll, med).resolve([]) == {}
    assert coll.calls == [[C1]]


@pytest.mark.parametrize(
    "coll_error, media_error",
    [
        (UpstreamUnavailableError("collections", "timeout"), None),
        (UpstreamRejectedError("collections", 403, "auth.forbidden"), None),
        (None, UpstreamUnavailableError("media_pipeline", "circuit_open")),
        (None, UpstreamRejectedError("media_pipeline", 403, "auth.forbidden")),
    ],
)
async def test_resolve_degrades_to_empty(
    coll_error: Exception | None, media_error: Exception | None
) -> None:
    coll = FakeCollections(coll_error or {C1: M1})
    med = FakeMedia(media_error or {M1: LINK})
    assert await covers(coll, med).resolve([C1]) == {}


async def test_resolve_deadline_and_missing_media_client() -> None:
    slow = FakeCollections({C1: M1}, delay=0.2)
    assert await covers(slow, FakeMedia({M1: LINK}), deadline_s=0.05).resolve([C1]) == {}
    assert await covers(FakeCollections({C1: M1}), None).resolve([C1]) == {}


async def test_resolve_cuts_batch_to_limit() -> None:
    coll = FakeCollections({})
    await covers(coll, FakeMedia(), batch_max=1).resolve([C1, C2])
    assert coll.calls == [[C1]]


async def test_get_covers_returns_null_for_every_asked_id() -> None:
    query = GetCollectionCovers(
        covers(FakeCollections({C1: M1}), FakeMedia({M1: LINK})), batch_max=50
    )
    assert await query([C1, C2, C1]) == {C1: LINK, C2: None}
