"""Сервис целиком: события catalog → задача → AssetsReady → ссылки; admin API.

Реальные Postgres, Redis, S3 (moto или SeaweedFS), ffmpeg и Pillow; очередь задач — фейк из testkit
(задачи выполняются вызовом обработчика воркера), TMDB — respx-заглушка.
"""

from __future__ import annotations

import base64
import dataclasses
import functools
import hashlib
import io
import subprocess
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import httpx
import pytest
import respx
import sqlalchemy as sa
from media_pipeline_service.application.dto import GENERATE_TASK
from media_pipeline_service.application.errors import GenerationError
from media_pipeline_service.application.ports import StorageArea
from media_pipeline_service.infrastructure.container import MediaAdapters
from media_pipeline_service.infrastructure.db.tables import TABLES
from media_pipeline_service.infrastructure.fetcher import HttpSourceFetcher
from media_pipeline_service.infrastructure.links import RedisLinkRateLimiter
from media_pipeline_service.infrastructure.storage import S3Storage
from media_pipeline_service.main import (
    DEFINITION,
    build_event_handlers,
    build_routers,
    build_task_handlers,
)
from media_pipeline_service.settings import MediaSettings, S3Settings, Settings
from PIL import Image
from pydantic import SecretStr
from redis.asyncio import Redis

from gv_core import uuid7
from gv_messaging import EventEnvelope, EventProcessor, Inbox, Outcome
from gv_service import Infrastructure, create_app
from gv_testkit import (
    FakeDeadLetterSink,
    InfraProvider,
    InMemoryTaskQueue,
    S3Endpoint,
    TestTokenIssuer,
    apply_migrations,
    assert_event_matches_schema,
    assert_problem,
    load_example,
    outbox_envelopes,
    service_client,
)

pytestmark = pytest.mark.component

SECRET = "component-test-secret-0123456789abcdef"
TMDB_IP = "93.184.216.34"
POSTER_PATH = "/t/p/original/abc.jpg"
ADMIN_PERMS = ("admin.access", "media.upload_source", "media.regenerate")


def _jpeg(width: int = 600, height: int = 900) -> bytes:
    image = Image.new("RGB", (width, height), (120, 40, 200))
    out = io.BytesIO()
    image.save(out, format="JPEG")
    return out.getvalue()


def _wav(seconds: int = 20) -> bytes:
    return subprocess.run(  # noqa: S603
        ["ffmpeg", "-v", "error", "-nostdin", "-f", "lavfi", "-i", f"sine=f=440:d={seconds}",  # noqa: S607
         "-ar", "8000", "-f", "wav", "-"],
        check=True, capture_output=True,
    ).stdout  # fmt: skip


@dataclass
class Service:
    client: httpx.AsyncClient
    infra: Infrastructure
    queue: InMemoryTaskQueue
    storage: S3Storage
    processor: EventProcessor
    tasks: dict[str, Any]
    issuer: TestTokenIssuer

    async def deliver(self, envelope: EventEnvelope) -> Outcome:
        return await self.processor.process(envelope.to_bytes(), source="test")

    async def run_tasks(self) -> int:
        done = 0
        while self.queue.tasks:
            task = self.queue.tasks.pop(0)
            assert task["task_type"] == GENERATE_TASK
            await self.tasks[GENERATE_TASK](task["payload"])
            done += 1
        return done

    async def outbox(self) -> list[EventEnvelope]:
        assert self.infra.sessions is not None
        async with self.infra.sessions() as session:
            return await outbox_envelopes(session, TABLES.outbox)

    def admin(self, *perms: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.issuer.issue(perms=perms or ADMIN_PERMS)}"}


@pytest.fixture
async def service(
    request: pytest.FixtureRequest,
    database_url: str,
    infra: InfraProvider,
    s3_endpoint: S3Endpoint,
    token_issuer: TestTokenIssuer,
) -> AsyncIterator[Service]:
    """Сервис на реальной инфраструктуре.

    ``indirect``-параметр ``{"redis_down": True}`` — лимитер ссылок смотрит в недоступный Redis.
    """
    options: dict[str, Any] = getattr(request, "param", {})
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    suffix = uuid7().hex[-12:]
    settings = Settings(
        database_url=SecretStr(database_url),
        redis_url=SecretStr(infra.redis_url()),
        s3=S3Settings(
            endpoint=s3_endpoint.url,
            access_key=SecretStr(s3_endpoint.access_key),
            secret_key=SecretStr(s3_endpoint.secret_key),
            bucket_sources=f"gv-src-{suffix}",
            bucket_assets=f"gv-assets-{suffix}",
        ),
        media=MediaSettings(
            signing_secret=SecretStr(SECRET),
            public_base_url="https://gv.test",
            link_max_per_window=3,
            max_upload_mb=1,
        ),
    )
    runtime = await Infrastructure.open(settings, TABLES)
    queue = InMemoryTaskQueue()

    async def tmdb_resolver(host: str) -> list[str]:
        return [TMDB_IP]

    adapters = dataclasses.replace(
        MediaAdapters.from_settings(runtime, settings),
        queue=queue,
        fetcher=HttpSourceFetcher(
            allowed_hosts=settings.media.source_host_list,
            max_bytes=10 * 1024 * 1024,
            timeout_s=10,
            resolver=tmdb_resolver,
        ),
    )
    if options.get("redis_down"):
        dead = Redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.2)
        adapters = dataclasses.replace(
            adapters,
            limiter=RedisLinkRateLimiter(
                dead, retry_after_s=settings.media.link_limiter_retry_after_s
            ),
        )
    assert isinstance(adapters.storage, S3Storage)
    await adapters.storage.ensure_buckets()
    definition = dataclasses.replace(
        DEFINITION, build_routers=functools.partial(build_routers, adapters=adapters)
    )
    assert runtime.sessions is not None
    processor = EventProcessor(
        group="media_pipeline.consumer",
        registry=build_event_handlers(runtime, adapters),
        session_factory=runtime.sessions,
        inbox=Inbox(TABLES.inbox),
        dead_letters=FakeDeadLetterSink(),
        clock=runtime.clock,
    )
    app = create_app(
        definition,
        runtime,
        jwt_verifier=token_issuer.verifier(),
        service_jwt_verifier=token_issuer.verifier(audience="media_pipeline"),
    )
    # internal-ручки вызывает game с сервисным токеном (D13): хук добавляет его в запросы к
    # /internal/ без своего заголовка (кроме помеченных ``no_service_token``).
    game = token_issuer.issue_service("game", "media_pipeline", ["assets.link"])

    async def add_service_token(request: httpx.Request) -> None:
        skip = request.extensions.get("no_service_token")
        if request.url.path.startswith("/internal/") and not skip:
            request.headers.setdefault("authorization", f"Bearer {game}")

    try:
        with respx.mock(assert_all_called=False) as router:
            router.get(f"https://{TMDB_IP}{POSTER_PATH}").respond(200, content=_jpeg())
            router.route(host="127.0.0.1").pass_through()  # S3 (moto или SeaweedFS)
            async with service_client(app) as client:
                client.event_hooks["request"].append(add_service_token)
                yield Service(
                    client, runtime, queue, adapters.storage, processor,
                    build_task_handlers(runtime, adapters), token_issuer,
                )  # fmt: skip
    finally:
        await runtime.close()


async def _ready_poster(service: Service) -> dict[str, Any]:
    # event_id из файла: тесты ниже доставляют тот же пример повторно и ждут дубль.
    created = load_example("catalog.MediaItemCreated", fresh_id=False)
    assert await service.deliver(created) is Outcome.PROCESSED
    assert await service.run_tasks() == 1
    item_id = created.payload["media_item_id"]
    response = await service.client.get(
        "/internal/v1/asset-sets", params={"media_item_id": item_id}
    )
    assert response.status_code == 200, response.text
    (asset_set,) = response.json()
    assert asset_set["status"] == "ready"
    return dict(asset_set)


# ---------------------------------------------------------------- события → ассеты → ссылки
async def test_created_event_to_assets_ready_and_signed_link(service: Service) -> None:
    asset_set = await _ready_poster(service)
    created = load_example("catalog.MediaItemCreated", fresh_id=False)
    assert asset_set["clue_type"] == "poster_masked"
    assert asset_set["levels_count"] == 5  # встроенный профиль

    (event,) = await service.outbox()
    assert_event_matches_schema(event)
    assert event.event_type == "media_pipeline.AssetsReady"
    assert event.payload["asset_set_id"] == asset_set["asset_set_id"]
    assert event.aggregate_id == created.payload["media_item_id"]

    # повтор того же события — дубль по inbox, новых задач нет
    assert await service.deliver(created) is Outcome.DUPLICATE
    assert service.queue.tasks == []

    before = int(datetime.now(UTC).timestamp())
    response = await service.client.post(
        "/internal/v1/asset-links",
        json={"asset_set_id": asset_set["asset_set_id"], "level": 2, "subject": "game:1"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["level"], body["mime"]) == (2, "image/webp")
    url = urlsplit(body["url"])
    query = parse_qs(url.query)
    expires = int(query["expires"][0])
    assert before + 59 <= expires <= before + 62
    assert datetime.fromisoformat(body["expires_at"]).timestamp() == expires
    assert url.netloc == "gv.test" and url.path.startswith("/media/a/")
    expected = (
        base64.urlsafe_b64encode(
            hashlib.md5(f"{expires}{url.path} {SECRET}".encode(), usedforsecurity=False).digest()
        )
        .decode()
        .rstrip("=")
    )
    assert query["md5"] == [expected]
    assert created.payload["media_item_id"] not in body["url"]

    key = url.path.removeprefix("/media/")
    data = await service.storage.get(StorageArea.ASSETS, key, max_bytes=10_000_000)
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP"


async def test_link_errors_follow_contract(service: Service) -> None:
    assert_problem(
        await service.client.post(
            "/internal/v1/asset-links",
            json={"asset_set_id": str(uuid7()), "level": 1, "subject": "s"},
        ),
        404,
        "asset.not_found",
    )
    await service.deliver(load_example("catalog.MediaItemCreated"))
    item_id = load_example("catalog.MediaItemCreated").payload["media_item_id"]
    (pending,) = (
        await service.client.get("/internal/v1/asset-sets", params={"media_item_id": item_id})
    ).json()
    link = {"asset_set_id": pending["asset_set_id"], "level": 1, "subject": f"s-{uuid7().hex}"}
    assert_problem(
        await service.client.post("/internal/v1/asset-links", json=link), 409, "asset.not_ready"
    )
    await service.run_tasks()
    assert_problem(
        await service.client.post("/internal/v1/asset-links", json={**link, "level": 6}),
        422,
        "asset.level_out_of_range",
    )
    for _ in range(3):
        assert (await service.client.post("/internal/v1/asset-links", json=link)).status_code == 200
    limited = assert_problem(
        await service.client.post("/internal/v1/asset-links", json=link), 429, "asset.rate_limited"
    )
    assert limited["status"] == 429
    assert_problem(
        await service.client.post("/internal/v1/asset-links", json={**link, "subject": ""}),
        422,
        "request.validation_failed",
    )


@pytest.mark.parametrize("service", [{"redis_down": True}], indirect=True)
async def test_link_limiter_down_is_503_with_retry_after(service: Service) -> None:
    asset_set = await _ready_poster(service)
    response = await service.client.post(
        "/internal/v1/asset-links",
        json={"asset_set_id": asset_set["asset_set_id"], "level": 1, "subject": "s"},
    )
    body = assert_problem(response, 503, "dependency.unavailable")
    assert body["details"] == {"retry_after": 5}
    assert response.headers["Retry-After"] == "5"


async def test_blocked_returns_410_and_unblock_regenerates(service: Service) -> None:
    asset_set = await _ready_poster(service)
    link = {"asset_set_id": asset_set["asset_set_id"], "level": 1, "subject": "s"}
    issued = (await service.client.post("/internal/v1/asset-links", json=link)).json()
    key = urlsplit(issued["url"]).path.removeprefix("/media/")
    assert await service.deliver(load_example("catalog.MediaItemBlocked")) is Outcome.PROCESSED
    assert_problem(
        await service.client.post("/internal/v1/asset-links", json=link), 410, "asset.blocked"
    )
    # объекты удалены сразу: уже выданная ссылка перестаёт работать до истечения TTL
    with pytest.raises(GenerationError):
        await service.storage.get(StorageArea.ASSETS, key, max_bytes=10_000_000)
    # старое Updated (version 2 < 3) не разблокирует
    await service.deliver(load_example("catalog.MediaItemUpdated"))
    assert_problem(
        await service.client.post("/internal/v1/asset-links", json=link), 410, "asset.blocked"
    )

    assert await service.deliver(load_example("catalog.MediaItemUnblocked")) is Outcome.PROCESSED
    assert await service.run_tasks() == 1
    assert (await service.client.post("/internal/v1/asset-links", json=link)).status_code == 200
    ready_events = [
        e for e in await service.outbox() if e.event_type == "media_pipeline.AssetsReady"
    ]
    assert [e.payload["version"] for e in ready_events] == sorted(
        e.payload["version"] for e in ready_events
    )
    assert len(ready_events) == 2


async def test_source_fetch_failure_publishes_assets_failed(service: Service) -> None:
    created = load_example("catalog.MediaItemCreated")
    payload = dict(created.payload)
    payload["source_media"] = [
        {**payload["source_media"][0], "uri": "https://image.tmdb.org/t/p/original/missing.jpg"}
    ]
    with respx.mock(assert_all_called=False) as router:
        router.get(f"https://{TMDB_IP}/t/p/original/missing.jpg").respond(404)
        router.route(host="127.0.0.1").pass_through()
        await service.deliver(created.model_copy(update={"payload": payload, "event_id": uuid7()}))
        await service.run_tasks()
    (event,) = await service.outbox()
    assert_event_matches_schema(event)
    assert event.event_type == "media_pipeline.AssetsFailed"
    assert (event.payload["error_code"], event.payload["retryable"]) == ("source.not_found", False)


async def test_malformed_catalog_event_goes_to_dlq(service: Service) -> None:
    created = load_example("catalog.MediaItemCreated")
    broken = created.model_copy(update={"payload": {"media_item_id": "x"}, "event_id": uuid7()})
    assert await service.deliver(broken) is Outcome.DEAD


# ---------------------------------------------------------------- admin API
def _music_item_event() -> tuple[EventEnvelope, str]:
    created = load_example("catalog.MediaItemCreated")
    item_id = str(uuid7())
    payload = {
        **created.payload,
        "media_item_id": item_id,
        "domain": "music",
        "kind": "track",
        "source_media": [],
    }
    return created.model_copy(
        update={"payload": payload, "event_id": uuid7(), "aggregate_id": item_id}
    ), item_id


async def test_admin_upload_audio_generates_audio_levels(service: Service) -> None:
    event, item_id = _music_item_event()
    await service.deliver(event)
    form = {"media_item_id": item_id, "kind": "audio", "reason": "лицензированный превью"}
    files = {"file": ("track.wav", _wav(), "audio/wav")}
    headers = {**service.admin(), "Idempotency-Key": f"upload-{uuid7().hex}"}
    response = await service.client.post(
        "/admin/v1/sources", data=form, files=files, headers=headers
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["mime"] == "audio/flac" and len(body["scheduled_asset_set_ids"]) == 1
    # повтор с тем же ключом — тот же ответ, без новой загрузки
    again = await service.client.post("/admin/v1/sources", data=form, files=files, headers=headers)
    assert (again.status_code, again.json()) == (201, body)
    assert len(service.queue.tasks) == 1

    await service.run_tasks()
    (asset_set,) = (
        await service.client.get("/internal/v1/asset-sets", params={"media_item_id": item_id})
    ).json()
    assert (asset_set["clue_type"], asset_set["status"], asset_set["levels_count"]) == (
        "audio_intro",
        "ready",
        6,
    )
    link = await service.client.post(
        "/internal/v1/asset-links",
        json={"asset_set_id": asset_set["asset_set_id"], "level": 6, "subject": "s"},
    )
    assert link.json()["mime"] == "audio/mp4"
    (event_out,) = await service.outbox()
    assert_event_matches_schema(event_out)


async def test_admin_upload_validation(service: Service) -> None:
    event, item_id = _music_item_event()
    await service.deliver(event)
    form = {"media_item_id": item_id, "kind": "audio", "reason": "причина"}
    wav = {"file": ("a.wav", _wav(1), "audio/wav")}

    def key() -> dict[str, str]:
        return {"Idempotency-Key": f"k-{uuid7().hex}"}

    post = service.client.post
    assert_problem(
        await post("/admin/v1/sources", data=form, files=wav, headers=key()),
        401,
        "auth.unauthenticated",
    )
    assert_problem(
        await post(
            "/admin/v1/sources",
            data=form,
            files=wav,
            headers={**service.admin("admin.access"), **key()},
        ),
        403,
        "auth.forbidden",
    )
    assert_problem(  # конкретное право без admin.access — тоже 403
        await post(
            "/admin/v1/sources",
            data=form,
            files=wav,
            headers={**service.admin("media.upload_source"), **key()},
        ),
        403,
        "auth.forbidden",
    )
    assert_problem(
        await post("/admin/v1/sources", data=form, files=wav, headers=service.admin()),
        422,
        "idempotency.key_required",
    )
    assert_problem(
        await post(
            "/admin/v1/sources",
            data=form,
            files=wav,
            headers={**service.admin(), "Idempotency-Key": "bad key!"},
        ),
        422,
        "idempotency.key_invalid",
    )
    fake = {"file": ("evil.wav", b"<?php system($_GET['c']); ?>", "audio/wav")}
    assert_problem(
        await post(
            "/admin/v1/sources", data=form, files=fake, headers={**service.admin(), **key()}
        ),
        422,
        "source.unsupported_type",
    )
    big = {"file": ("big.wav", b"RIFF\0\0\0\0WAVE" + bytes(1024 * 1024), "audio/wav")}
    assert_problem(
        await post("/admin/v1/sources", data=form, files=big, headers={**service.admin(), **key()}),
        422,
        "source.too_large",
    )
    assert_problem(
        await post(
            "/admin/v1/sources",
            data={**form, "reason": "  a "},  # Reason: пробелы срезаются, затем длина 3..500
            files=wav,
            headers={**service.admin(), **key()},
        ),
        422,
        "request.validation_failed",
    )
    assert_problem(
        await post(
            "/admin/v1/sources",
            data={**form, "media_item_id": str(uuid7())},
            files=wav,
            headers={**service.admin(), **key()},
        ),
        404,
        "media_item.not_found",
    )


async def test_admin_regenerate(service: Service) -> None:
    asset_set = await _ready_poster(service)
    url = f"/admin/v1/asset-sets/{asset_set['asset_set_id']}/regenerate"
    headers = {**service.admin(), "Idempotency-Key": f"regen-{uuid7().hex}"}
    assert_problem(
        await service.client.post(url, json={"reason": "x"}, headers=headers),
        422,
        "request.validation_failed",
    )
    response = await service.client.post(
        url, json={"reason": "надпись на постере"}, headers=headers
    )
    assert response.status_code == 202, response.text
    assert (response.json()["status"], response.json()["generation"]) == ("pending", 2)
    replay = await service.client.post(url, json={"reason": "надпись на постере"}, headers=headers)
    assert replay.json() == response.json()
    reused = await service.client.post(url, json={"reason": "другая причина"}, headers=headers)
    assert_problem(reused, 409, "idempotency.key_reused")
    assert len(service.queue.tasks) == 1
    await service.run_tasks()

    listed = await service.client.get(
        "/admin/v1/asset-sets",
        params={"media_item_id": asset_set["media_item_id"]},
        headers=service.admin(),
    )
    assert listed.json()[0]["generation"] == 2
    assert_problem(
        await service.client.post(
            f"/admin/v1/asset-sets/{uuid7()}/regenerate",
            json={"reason": "нет такого"},
            headers={**service.admin(), "Idempotency-Key": f"r-{uuid7().hex}"},
        ),
        404,
        "asset.not_found",
    )
    await service.deliver(load_example("catalog.MediaItemBlocked"))
    assert_problem(
        await service.client.post(
            url,
            json={"reason": "после блокировки"},
            headers={**service.admin(), "Idempotency-Key": f"r-{uuid7().hex}"},
        ),
        410,
        "asset.blocked",
    )
    for perms in (("admin.access",), ("media.regenerate",)):
        assert_problem(
            await service.client.post(
                url,
                json={"reason": "без прав"},
                headers={**service.admin(*perms), "Idempotency-Key": f"r-{uuid7().hex}"},
            ),
            403,
            "auth.forbidden",
        )
    assert_problem(
        await service.client.post(url, json={"reason": "без ключа"}, headers=service.admin()),
        422,
        "idempotency.key_required",
    )


async def test_admin_asset_sets_requires_admin_access(service: Service) -> None:
    asset_set = await _ready_poster(service)
    params = {"media_item_id": asset_set["media_item_id"]}
    assert_problem(
        await service.client.get("/admin/v1/asset-sets", params=params), 401, "auth.unauthenticated"
    )
    assert_problem(
        await service.client.get(
            "/admin/v1/asset-sets", params=params, headers=service.admin("media.regenerate")
        ),
        403,
        "auth.forbidden",
    )
    listed = await service.client.get(
        "/admin/v1/asset-sets", params=params, headers=service.admin("admin.access")
    )
    assert listed.status_code == 200
    assert [s["asset_set_id"] for s in listed.json()] == [asset_set["asset_set_id"]]


async def test_asset_sets_requires_media_item_id(service: Service) -> None:
    assert_problem(
        await service.client.get("/internal/v1/asset-sets"), 422, "request.validation_failed"
    )
    response = await service.client.get(
        "/internal/v1/asset-sets", params={"media_item_id": str(UUID(int=1))}
    )
    assert response.json() == []


# ---------------------------------------------------------------- сервисные токены (D13)
async def test_internal_api_requires_service_token_with_scope(service: Service) -> None:
    asset_set = await _ready_poster(service)
    link = {"asset_set_id": asset_set["asset_set_id"], "level": 1, "subject": "sess:auth"}
    media_item_id = asset_set["media_item_id"]

    async def call(method: str, auth: str | None) -> httpx.Response:
        if method == "POST":
            request = service.client.build_request("POST", "/internal/v1/asset-links", json=link)
        else:
            request = service.client.build_request(
                "GET", "/internal/v1/asset-sets", params={"media_item_id": media_item_id}
            )
        if auth is None:
            request.extensions["no_service_token"] = True
        else:
            request.headers["authorization"] = f"Bearer {auth}"
        return await service.client.send(request)

    issuer = service.issuer
    for method in ("POST", "GET"):
        # без токена
        assert_problem(await call(method, None), 401, "auth.unauthenticated")
        # пользовательский JWT (даже админский) internal не проходит
        user = issuer.issue(perms=ADMIN_PERMS)
        assert_problem(await call(method, user), 401, "auth.token_invalid")
        # сервисный токен другому адресату
        to_identity = issuer.issue_service("game", "identity", ["assets.link"])
        assert_problem(await call(method, to_identity), 401, "auth.token_invalid")
        # сервисный токен без нужной области
        no_scope = issuer.issue_service("admin", "media_pipeline", ["admin_sessions"])
        assert_problem(await call(method, no_scope), 403, "auth.forbidden")
        # токен game с assets.link
        game = issuer.issue_service("game", "media_pipeline", ["assets.link"])
        assert (await call(method, game)).status_code == 200


async def test_link_issuance_journal_records_client_id(service: Service) -> None:
    asset_set = await _ready_poster(service)
    response = await service.client.post(
        "/internal/v1/asset-links",
        json={"asset_set_id": asset_set["asset_set_id"], "level": 1, "subject": "sess:journal"},
    )
    assert response.status_code == 200, response.text
    journal = TABLES.metadata.tables["link_issuance"]
    assert service.infra.sessions is not None
    async with service.infra.sessions() as session:
        rows = (await session.execute(sa.select(journal.c.client_id, journal.c.subject_hash))).all()
    assert [(r.client_id, r.subject_hash) for r in rows] == [
        ("game", hashlib.sha256(b"sess:journal").hexdigest())
    ]
