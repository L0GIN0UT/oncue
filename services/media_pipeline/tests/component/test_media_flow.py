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
import json
import stat
import subprocess
import zipfile
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
import yaml
from media_pipeline_service.application.dto import GENERATE_TASK, PACKAGE_TASK
from media_pipeline_service.application.errors import GenerationError
from media_pipeline_service.application.ports import StorageArea
from media_pipeline_service.infrastructure.config import read_profiles_document
from media_pipeline_service.infrastructure.container import MediaAdapters
from media_pipeline_service.infrastructure.db.tables import TABLES
from media_pipeline_service.infrastructure.fetcher import HttpSourceFetcher
from media_pipeline_service.infrastructure.links import RedisLinkRateLimiter
from media_pipeline_service.infrastructure.storage import S3Storage
from media_pipeline_service.main import (
    DEFINITION,
    build_event_handlers,
    build_periodic_jobs,
    build_routers,
    build_task_handlers,
)
from media_pipeline_service.settings import MediaSettings, S3Settings, Settings
from PIL import Image
from pydantic import SecretStr
from redis.asyncio import Redis

from gv_core import uuid7
from gv_messaging import EventEnvelope, EventProcessor, Inbox, Outcome
from gv_service import Environment, Infrastructure, JobResult, ServiceDefinition, create_app
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
    run_periodic_job,
    service_client,
)

pytestmark = pytest.mark.component

SECRET = "component-test-secret-0123456789abcdef"
TMDB_IP = "93.184.216.34"
POSTER_PATH = "/t/p/original/abc.jpg"
ADMIN_PERMS = ("admin.access", "media.upload_source", "media.regenerate")
# Происхождение исходника (D6); обязательно вне local/ci.
PROVENANCE = {
    "origin": "upload",
    "source_url": "https://example.org/track",
    "license_note": "синтетический тон, без прав третьих лиц",
}


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
    definition: ServiceDefinition

    async def source_keys(self) -> list[str]:
        """Ключи объектов бакета исходников."""
        keys: list[str] = []
        token = None
        while True:
            page = await self.storage.list_objects(StorageArea.SOURCES, "", limit=1000, token=token)
            keys += [o.key for o in page.items]
            token = page.next_token
            if token is None:
                return keys

    async def run_job(self, name: str) -> JobResult:
        return await run_periodic_job(self.definition, name, self.infra)

    async def deliver(self, envelope: EventEnvelope) -> Outcome:
        return await self.processor.process(envelope.to_bytes(), source="test")

    async def run_tasks(self) -> int:
        done = 0
        while self.queue.tasks:
            task = self.queue.tasks.pop(0)
            assert task["task_type"] in {GENERATE_TASK, PACKAGE_TASK}
            await self.tasks[task["task_type"]](task["payload"])
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

    ``indirect``-параметры: ``{"redis_down": True}`` — лимитер ссылок смотрит в недоступный
    Redis; ``{"environment": "production"}`` — окружение сервиса (по умолчанию ``ci``);
    ``{"with_artwork": True}`` — со встроенными профилями ``artwork`` (по умолчанию они убраны,
    чтобы счёт наборов в остальных тестах не зависел от картинки ответа).
    """
    options: dict[str, Any] = getattr(request, "param", {})
    profiles_path = None
    if not options.get("with_artwork"):
        document = read_profiles_document()
        document["profiles"] = [p for p in document["profiles"] if p["clue_type"] != "artwork"]
        profiles_path = request.getfixturevalue("tmp_path") / "asset_profiles.yaml"
        profiles_path.write_text(yaml.safe_dump(document, allow_unicode=True), encoding="utf-8")
    assert DEFINITION.migrations_path is not None
    await apply_migrations(database_url, DEFINITION.migrations_path)
    suffix = uuid7().hex[-12:]
    settings = Settings(
        environment=Environment(options.get("environment", "ci")),
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
            package_max_archive_mb=options.get("package_max_archive_mb", 1024),
            package_max_ratio=options.get("package_max_ratio", 100),
            profiles_path=profiles_path,
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
        DEFINITION,
        build_routers=functools.partial(build_routers, adapters=adapters),
        build_periodic_jobs=functools.partial(build_periodic_jobs, adapters=adapters),
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
                    build_task_handlers(runtime, adapters), token_issuer, definition,
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
@pytest.mark.parametrize("service", [{"with_artwork": True}], indirect=True)
async def test_film_poster_also_builds_artwork_and_issues_its_link(service: Service) -> None:
    """Картинка ответа (ADR 0073): постер фильма даёт одноуровневый набор ``artwork`` 600×900."""
    created = load_example("catalog.MediaItemCreated", fresh_id=False)
    assert await service.deliver(created) is Outcome.PROCESSED
    assert await service.run_tasks() == 2  # poster_masked + artwork
    item_id = created.payload["media_item_id"]
    response = await service.client.get(
        "/internal/v1/asset-sets", params={"media_item_id": item_id}
    )
    by_type = {s["clue_type"]: s for s in response.json()}
    assert set(by_type) == {"poster_masked", "artwork"}
    artwork = by_type["artwork"]
    assert (artwork["status"], artwork["levels_count"]) == ("ready", 1)
    ready = [e for e in await service.outbox() if e.event_type == "media_pipeline.AssetsReady"]
    assert {e.payload["clue_type"] for e in ready} == {"poster_masked", "artwork"}
    for event in ready:
        assert_event_matches_schema(event)
    link = await service.client.post(
        "/internal/v1/asset-links",
        json={"asset_set_id": artwork["asset_set_id"], "level": 1, "subject": "game:1"},
    )
    assert link.status_code == 200, link.text
    assert link.json()["mime"] == "image/webp"


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
    form = {
        "media_item_id": item_id,
        "kind": "audio",
        "reason": "лицензированный превью",
        **PROVENANCE,
    }
    # 50 с: хватает на audio_reverse/default (start_s = 30, до 15 с).
    files = {"file": ("track.wav", _wav(50), "audio/wav")}
    headers = {**service.admin(), "Idempotency-Key": f"upload-{uuid7().hex}"}
    response = await service.client.post(
        "/admin/v1/sources", data=form, files=files, headers=headers
    )
    assert response.status_code == 201, response.text
    body = response.json()
    # audio_intro/default, audio_intro/preview, audio_intro/duel (4a), audio_reverse/default —
    # все от одного аудио
    assert body["mime"] == "audio/flac" and len(body["scheduled_asset_set_ids"]) == 4
    assert (body["position"], body["origin"]) == (1, "upload")
    # повтор с тем же ключом — тот же ответ, без новой загрузки
    again = await service.client.post("/admin/v1/sources", data=form, files=files, headers=headers)
    assert (again.status_code, again.json()) == (201, body)
    assert len(service.queue.tasks) == 4

    await service.run_tasks()
    sets = (
        await service.client.get("/internal/v1/asset-sets", params={"media_item_id": item_id})
    ).json()
    assert sorted(
        (s["clue_type"], s["profile_key"], s["status"], s["levels_count"]) for s in sets
    ) == [
        ("audio_intro", "default", "ready", 6),
        ("audio_intro", "duel", "ready", 2),
        ("audio_intro", "preview", "ready", 1),
        ("audio_reverse", "default", "ready", 6),
    ]
    for asset_set in sets:
        link = await service.client.post(
            "/internal/v1/asset-links",
            json={"asset_set_id": asset_set["asset_set_id"], "level": 1, "subject": "s"},
        )
        assert link.json()["mime"] == "audio/mp4"
    events = await service.outbox()
    assert len(events) == 4
    for event_out in events:
        assert_event_matches_schema(event_out)


async def test_admin_upload_validation(service: Service) -> None:
    event, item_id = _music_item_event()
    await service.deliver(event)
    form = {"media_item_id": item_id, "kind": "audio", "reason": "причина", **PROVENANCE}
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


async def test_admin_sources_lists_catalog_sources_by_kind_and_position(
    service: Service,
) -> None:
    """Хвост 3a (план 3b, M3): исходники единицы для карточки в админке — `admin.access`."""
    created, item_id = _film_with_texts(1, "Учёные … ищут новый дом.")
    frame = {
        "source_media_ref": str(uuid7()),
        "kind": "frame",
        "origin": "tmdb",
        "uri": f"https://image.tmdb.org{POSTER_PATH}",
        "position": 2,
    }
    poster = {**frame, "source_media_ref": str(uuid7()), "kind": "poster", "position": 1}
    first_frame = {**frame, "source_media_ref": str(uuid7()), "position": 1}
    created = created.model_copy(
        update={"payload": {**created.payload, "source_media": [frame, poster, first_frame]}}
    )
    assert await service.deliver(created) is Outcome.PROCESSED
    # свои эмодзи сборника — исходник со scope: в списке админки его нет
    collection, _ = _collection_event(1, {item_id: SIX_EMOJI})
    assert await service.deliver(collection) is Outcome.PROCESSED

    params = {"media_item_id": item_id}
    response = await service.client.get(
        "/admin/v1/sources", params=params, headers=service.admin("admin.access")
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert [(s["kind"], s["position"]) for s in body] == [
        ("frame", 1), ("frame", 2), ("poster", 1), ("text", 1),
    ]  # fmt: skip
    assert [s["source_media_id"] for s in body[:3]] == [
        first_frame["source_media_ref"], frame["source_media_ref"], poster["source_media_ref"],
    ]  # fmt: skip
    assert {s["origin"] for s in body} == {"tmdb", "catalog"}
    assert set(body[0]) == {
        "source_media_id", "kind", "position", "origin", "status", "created_at",
        "media_item_id", "source_url", "license_note", "source_missing", "license_missing",
    }  # fmt: skip
    assert "uri" not in json.dumps(body)

    unknown = await service.client.get(
        "/admin/v1/sources",
        params={"media_item_id": str(uuid7())},
        headers=service.admin("admin.access"),
    )
    assert unknown.status_code == 200 and unknown.json() == []
    assert_problem(
        await service.client.get("/admin/v1/sources", params=params), 401, "auth.unauthenticated"
    )
    assert_problem(
        await service.client.get(
            "/admin/v1/sources", params=params, headers=service.admin("media.regenerate")
        ),
        403,
        "auth.forbidden",
    )
    assert_problem(
        await service.client.get("/admin/v1/sources", headers=service.admin("admin.access")),
        422,
        "request.validation_failed",
    )


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


async def test_clue_assets_requested_creates_dynamic_profile_and_backfills(
    service: Service,
) -> None:
    """ADR 0025: событие game → динамический профиль → досоздание задачей relay → AssetsReady
    с params_hash из события; повтор события и повтор запроса ничего не дублируют."""
    film, item_id = _film_with_texts(1, "Учёные … ищут новый дом.")
    await service.deliver(film)
    await service.run_tasks()  # description/default и emoji/default

    requested = load_example("game.ClueAssetsRequested", fresh_id=False)
    assert await service.deliver(requested) is Outcome.PROCESSED
    assert await service.deliver(requested) is Outcome.DUPLICATE
    again = requested.model_copy(
        update={"event_id": uuid7(), "payload": {**requested.payload, "request_id": str(uuid7())}}
    )
    assert await service.deliver(again) is Outcome.PROCESSED
    published = load_example("game.ModeConfigPublished")
    assert await service.deliver(published) is Outcome.SKIPPED

    hash_ = requested.payload["params_hash"]
    url = "/admin/v1/profiles"
    query = {"clue_type": "description", "params_hash": hash_}
    (profile,) = (await service.client.get(url, params=query, headers=service.admin())).json()
    assert (profile["origin"], profile["status"], profile["profile_key"]) == (
        "dynamic",
        "backfilling",
        None,
    )
    assert profile["sets"] == {"ready": 0, "pending": 0, "failed": 0}

    assert await service.run_job("media.profiles.backfill") is JobResult.OK
    assert await service.run_tasks() == 1
    (profile,) = (await service.client.get(url, params=query, headers=service.admin())).json()
    assert (profile["status"], profile["sets"]) == (
        "active",
        {"ready": 1, "pending": 0, "failed": 0},
    )
    ready = [
        e
        for e in await service.outbox()
        if e.event_type == "media_pipeline.AssetsReady" and e.payload["params_hash"] == hash_
    ]
    assert len(ready) == 1
    assert_event_matches_schema(ready[0])
    assert ready[0].payload["media_item_id"] == item_id
    assert ready[0].payload["profile_key"] == f"dyn_{hash_[:16]}"
    assert ready[0].payload["levels_count"] == 2


async def test_admin_profiles_lists_static_and_requires_admin_access(service: Service) -> None:
    url = "/admin/v1/profiles"
    assert_problem(await service.client.get(url), 401, "auth.unauthenticated")
    assert_problem(
        await service.client.get(url, headers=service.admin("media.regenerate")),
        403,
        "auth.forbidden",
    )
    listed = await service.client.get(url, headers=service.admin("admin.access"))
    assert listed.status_code == 200, listed.text
    keys = {(p["clue_type"], p["profile_key"]) for p in listed.json() if p["origin"] == "static"}
    assert {
        ("frame_progressive", "still"),
        ("audio_intro", "preview"),
        ("emoji", "default"),
    } <= keys
    assert_problem(
        await service.client.get(
            url, params={"params_hash": "nothex"}, headers=service.admin("admin.access")
        ),
        422,
        "request.validation_failed",
    )


async def test_admin_source_position(service: Service) -> None:
    created = load_example("catalog.MediaItemCreated")
    await service.deliver(created)
    (source_ref,) = [s["source_media_ref"] for s in created.payload["source_media"]]
    url = f"/admin/v1/sources/{source_ref}/position"
    headers = {**service.admin(), "Idempotency-Key": f"pos-{uuid7().hex}"}
    response = await service.client.put(
        url, json={"position": 3, "reason": "порядок кадров"}, headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["position"], body["kind"], body["origin"]) == (3, "poster", "tmdb")
    replay = await service.client.put(
        url, json={"position": 3, "reason": "порядок кадров"}, headers=headers
    )
    assert replay.json() == body
    assert_problem(
        await service.client.put(
            url, json={"position": 4, "reason": "порядок кадров"}, headers=headers
        ),
        409,
        "idempotency.key_reused",
    )
    assert_problem(
        await service.client.put(
            f"/admin/v1/sources/{uuid7()}/position",
            json={"position": 2, "reason": "нет такого"},
            headers={**service.admin(), "Idempotency-Key": f"pos-{uuid7().hex}"},
        ),
        404,
        "source.not_found",
    )
    assert_problem(
        await service.client.put(
            url,
            json={"position": 0, "reason": "вне диапазона"},
            headers={**service.admin(), "Idempotency-Key": f"pos-{uuid7().hex}"},
        ),
        422,
        "request.validation_failed",
    )
    assert_problem(
        await service.client.put(
            url,
            json={"position": 2, "reason": "без права"},
            headers={**service.admin("admin.access"), "Idempotency-Key": f"pos-{uuid7().hex}"},
        ),
        403,
        "auth.forbidden",
    )
    assert_problem(
        await service.client.put(
            url, json={"position": 2, "reason": "без ключа"}, headers=service.admin()
        ),
        422,
        "idempotency.key_required",
    )


@pytest.mark.parametrize("service", [{"environment": "production"}], indirect=True)
async def test_admin_upload_provenance_is_optional_everywhere(service: Service) -> None:
    """ADR 0072: на проде загрузка без источника и лицензии проходит и помечается."""
    event, item_id = _music_item_event()
    await service.deliver(event)
    form = {"media_item_id": item_id, "kind": "audio", "reason": "лицензированный превью"}
    files = {"file": ("track.wav", _wav(1), "audio/wav")}
    headers = {**service.admin(), "Idempotency-Key": f"upload-{uuid7().hex}"}
    uploaded = await service.client.post(
        "/admin/v1/sources", data=form, files=files, headers=headers
    )
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    assert (body["origin"], body["source_missing"], body["license_missing"]) == (
        "upload",
        True,
        True,
    )
    listed = await service.client.get(
        "/admin/v1/sources", params={"without_attribution": "true"}, headers=service.admin()
    )
    assert [i["source_media_id"] for i in listed.json()] == [body["source_media_id"]]
    assert listed.json()[0]["license_note"] is None
    assert_problem(
        await service.client.get("/admin/v1/sources", headers=service.admin()),
        422,
        "request.validation_failed",
    )
    assert_problem(
        await service.client.post(
            "/admin/v1/sources", data={**form, "kind": "text"}, files=files, headers=headers
        ),
        422,
        "request.validation_failed",
    )


# ---------------------------------------------------------------- тексты загадок (ADR 0024)
def _film_with_texts(version: int, plot: str) -> tuple[EventEnvelope, str]:
    created = load_example("catalog.MediaItemCreated")
    item_id = str(uuid7())
    payload = {
        **created.payload,
        "media_item_id": item_id,
        "version": version,
        "source_media": [],
        "persons": [
            {"person_id": str(uuid7()), "name": "Режиссёр Синтетический", "role": "director",
             "order": 0},
            *[
                {"person_id": str(uuid7()), "name": f"Актёр {i}", "role": "actor", "order": i}
                for i in range(1, 5)
            ],
        ],
        "countries": ["US", "GB"],
        "clue_texts": {"plot_masked": plot, "tagline": "Слоган", "emoji": "🚀🌌⏳🕳️👨‍🚀🌽🧑‍🔬"},
    }  # fmt: skip
    envelope = created.model_copy(
        update={"payload": payload, "event_id": uuid7(), "aggregate_id": item_id}
    )
    return envelope, item_id


async def test_clue_texts_become_json_assets_and_edit_regenerates(service: Service) -> None:
    created, item_id = _film_with_texts(1, "Учёные … ищут новый дом.")
    assert await service.deliver(created) is Outcome.PROCESSED
    assert await service.run_tasks() == 2
    sets = {
        s["clue_type"]: s
        for s in (
            await service.client.get("/internal/v1/asset-sets", params={"media_item_id": item_id})
        ).json()
    }
    assert set(sets) == {"description", "emoji"}
    assert all(s["status"] == "ready" and s["levels_count"] == 6 for s in sets.values())

    link = await service.client.post(
        "/internal/v1/asset-links",
        json={"asset_set_id": sets["description"]["asset_set_id"], "level": 2, "subject": "s"},
    )
    body = link.json()
    assert body["mime"] == "application/json"
    path = urlsplit(body["url"]).path
    assert path.startswith("/media/a/") and path.endswith(".json")
    document = json.loads(
        await service.storage.get(
            StorageArea.ASSETS, path.removeprefix("/media/"), max_bytes=100_000
        )
    )
    assert document == {
        "version": 1,
        "blocks": [
            {"kind": "tagline", "text": "Слоган"},
            {"kind": "plot", "text": "Учёные … ищут новый дом."},
        ],
    }
    assert "Интерстеллар" not in json.dumps(document, ensure_ascii=False)

    # Правка одобренного текста → новый текстовый исходник → новое поколение наборов.
    edited, _ = _film_with_texts(2, "Исследователи … летят сквозь червоточину.")
    payload = {**edited.payload, "media_item_id": item_id}
    update = edited.model_copy(
        update={
            "payload": payload,
            "aggregate_id": item_id,
            "event_type": "catalog.MediaItemUpdated",
        }
    )
    assert await service.deliver(update) is Outcome.PROCESSED
    assert await service.run_tasks() == 2
    regenerated = {
        s["clue_type"]: s
        for s in (
            await service.client.get("/internal/v1/asset-sets", params={"media_item_id": item_id})
        ).json()
    }
    assert {s["generation"] for s in regenerated.values()} == {2}
    assert {s["asset_set_id"] for s in regenerated.values()} == {
        s["asset_set_id"] for s in sets.values()
    }
    ready = [e for e in await service.outbox() if e.event_type == "media_pipeline.AssetsReady"]
    assert len(ready) == 4
    for event in ready:
        assert_event_matches_schema(event)


# ---------------------------------------------------------------- свои загадки сборников (ADR 0029)
# Встроенный профиль emoji открывает до 6 эмодзи: короче — набора нет (как у каталожных).
SIX_EMOJI = "🚢🧊💔🎻🌊🚪"


def _collection_event(
    version: int,
    items: dict[str, str | None],
    *,
    collection_id: str | None = None,
    event_type: str = "collections.CollectionPublished",
    status: str = "published",
) -> tuple[EventEnvelope, str]:
    example = load_example("collections.CollectionPublished")
    collection_id = collection_id or str(uuid7())
    payload = {
        **example.payload,
        "collection_id": collection_id,
        "version": version,
        "status": status,
        "items": [
            {"media_item_id": item, "position": i, **({"custom": {"emoji": e}} if e else {})}
            for i, (item, e) in enumerate(items.items())
        ],
    }
    envelope = example.model_copy(
        update={
            "payload": payload,
            "aggregate_id": collection_id,
            "event_type": event_type,
            "event_id": uuid7(),
        }
    )
    assert_event_matches_schema(envelope)
    return envelope, collection_id


async def _plain_film(service: Service) -> str:
    created = load_example("catalog.MediaItemCreated")
    item_id = str(uuid7())
    payload = {**created.payload, "media_item_id": item_id, "source_media": []}
    payload.pop("clue_texts", None)
    envelope = created.model_copy(
        update={"payload": payload, "event_id": uuid7(), "aggregate_id": item_id}
    )
    assert await service.deliver(envelope) is Outcome.PROCESSED
    return item_id


async def _sets(service: Service, item_id: str) -> list[dict[str, Any]]:
    response = await service.client.get(
        "/internal/v1/asset-sets", params={"media_item_id": item_id}
    )
    assert response.status_code == 200, response.text
    return list(response.json())


async def test_collection_emoji_to_assets_ready_with_scope(service: Service) -> None:
    item_id, plain_id = await _plain_film(service), await _plain_film(service)
    published, collection_id = _collection_event(3, {item_id: SIX_EMOJI, plain_id: None})
    assert await service.deliver(published) is Outcome.PROCESSED
    assert await service.run_tasks() == 1

    (scoped,) = await _sets(service, item_id)
    assert scoped["scope"] == {"kind": "collection", "id": collection_id}
    assert (scoped["clue_type"], scoped["status"], scoped["levels_count"]) == ("emoji", "ready", 6)
    assert await _sets(service, plain_id) == []

    (ready,) = [e for e in await service.outbox() if e.event_type == "media_pipeline.AssetsReady"]
    assert_event_matches_schema(ready)
    assert ready.aggregate_id == item_id
    assert ready.payload["scope"] == {"kind": "collection", "id": collection_id}
    assert ready.payload["asset_set_id"] == scoped["asset_set_id"]

    link = await service.client.post(
        "/internal/v1/asset-links",
        json={"asset_set_id": scoped["asset_set_id"], "level": 2, "subject": "game:c1"},
    )
    assert link.status_code == 200, link.text
    key = urlsplit(link.json()["url"]).path.removeprefix("/media/")
    document = json.loads(await service.storage.get(StorageArea.ASSETS, key, max_bytes=10_000))
    assert document == {"version": 1, "blocks": [{"kind": "emoji", "text": "🚢🧊"}]}

    # повтор того же события — дубль inbox; та же версия новым событием — устарело, no-op
    assert await service.deliver(published) is Outcome.DUPLICATE
    again = published.model_copy(update={"event_id": uuid7()})
    assert await service.deliver(again) is Outcome.PROCESSED
    assert service.queue.tasks == []
    assert await _sets(service, item_id) == [scoped]


async def test_collection_emoji_edit_regenerates_and_hide_delete_drop_sets(
    service: Service,
) -> None:
    item_id = await _plain_film(service)
    first, collection_id = _collection_event(1, {item_id: SIX_EMOJI})
    assert await service.deliver(first) is Outcome.PROCESSED
    await service.run_tasks()
    (initial,) = await _sets(service, item_id)

    edited, _ = _collection_event(
        2,
        {item_id: "🎬🍿🎥🎞️📽️🎟️"},
        collection_id=collection_id,
        event_type="collections.CollectionUpdated",
    )
    assert await service.deliver(edited) is Outcome.PROCESSED
    assert await service.run_tasks() == 1
    (regenerated,) = await _sets(service, item_id)
    assert regenerated["asset_set_id"] == initial["asset_set_id"]
    assert (regenerated["generation"], regenerated["status"]) == (2, "ready")

    hidden, _ = _collection_event(
        3,
        {item_id: "🎬🍿🎥🎞️📽️🎟️"},
        collection_id=collection_id,
        event_type="collections.CollectionUpdated",
        status="hidden",
    )
    assert await service.deliver(hidden) is Outcome.PROCESSED
    assert await _sets(service, item_id) == []
    gone = await service.client.post(
        "/internal/v1/asset-links",
        json={"asset_set_id": initial["asset_set_id"], "level": 1, "subject": "game:c1"},
    )
    assert_problem(gone, 404, "asset.not_found")

    republished, _ = _collection_event(4, {item_id: SIX_EMOJI}, collection_id=collection_id)
    assert await service.deliver(republished) is Outcome.PROCESSED
    await service.run_tasks()
    assert len(await _sets(service, item_id)) == 1

    deleted = load_example("collections.CollectionDeleted")
    deleted = deleted.model_copy(
        update={
            "payload": {**deleted.payload, "collection_id": collection_id, "version": 5},
            "aggregate_id": collection_id,
            "event_id": uuid7(),
        }
    )
    assert_event_matches_schema(deleted)
    assert await service.deliver(deleted) is Outcome.PROCESSED
    assert await _sets(service, item_id) == []
    assert service.infra.sessions is not None
    async with service.infra.sessions() as session:
        scoped_sources = (
            await session.execute(
                sa.text("SELECT count(*) FROM source_media WHERE scope_id = :id"),
                {"id": UUID(collection_id)},
            )
        ).scalar_one()
        state = (
            await session.execute(
                sa.text("SELECT status, version FROM collections_state WHERE collection_id = :id"),
                {"id": UUID(collection_id)},
            )
        ).one()
    assert scoped_sources == 0
    assert tuple(state) == ("deleted", 5)


async def test_collection_with_invalid_emoji_goes_to_dlq(service: Service) -> None:
    item_id = await _plain_film(service)
    # по схеме события допустимо (строка 1..128), но вне строгого набора RGI
    published, _ = _collection_event(1, {item_id: "abc🚢"})
    assert await service.deliver(published) is Outcome.DEAD
    assert await _sets(service, item_id) == []
    assert service.queue.tasks == []


# ---------------------------------------------------------------- решения модерации social (5c)
async def _approved_avatar(service: Service) -> UUID:
    asset = uuid7()
    assert service.infra.sessions is not None
    async with service.infra.sessions() as session, session.begin():
        await session.execute(
            sa.text(
                "INSERT INTO ugc_images (id, owner_user_id, purpose, status, public_key, width, "
                "height, bytes, sha256, submitted_at, version) VALUES (:id, :o, 'avatar', "
                "'approved', :k, 512, 512, 100, :h, now(), 1)"
            ),
            {"id": asset, "o": uuid7(), "k": f"u/{uuid7().hex}.webp", "h": "0" * 64},
        )
    return asset


def _moderated(asset: UUID, **changes: str) -> EventEnvelope:
    base = load_example("social.ContentModerated")
    payload = {
        **base.payload,
        "decision": "content_removed",
        "target_kind": "avatar",
        "target_id": str(asset),
        **changes,
    }
    return base.model_copy(update={"payload": payload, "event_id": uuid7()})


async def test_social_content_removed_takes_avatar_down_once(service: Service) -> None:
    asset = await _approved_avatar(service)
    assert await service.deliver(_moderated(asset)) is Outcome.PROCESSED
    (event,) = [
        e for e in await service.outbox() if e.event_type == "media_pipeline.AssetModerated"
    ]
    assert_event_matches_schema(event)
    assert (event.payload["status"], event.payload["version"]) == ("rejected", 2)
    assert event.payload["public_key"] is None and event.payload["reason_code"] == "inappropriate"
    async with service.infra.sessions() as session:  # type: ignore[misc]
        row = (
            await session.execute(
                sa.text("SELECT status, public_key FROM ugc_images WHERE id = :i"), {"i": asset}
            )
        ).one()
        queued = await session.scalar(sa.text("SELECT count(*) FROM ugc_object_purge"))
    assert tuple(row) == ("rejected", None) and queued == 1  # u/ уйдёт задачей media.ugc.purge
    # Новое событие по тому же ассету (другой кейс) и чужие решения - без новых событий.
    again = _moderated(asset, case_id=str(uuid7()))
    assert await service.deliver(again) is Outcome.PROCESSED
    dismissed = _moderated(asset, case_id=str(uuid7()), decision="dismissed")
    assert await service.deliver(dismissed) is Outcome.PROCESSED
    assert await service.deliver(_moderated(uuid7(), case_id=str(uuid7()))) is Outcome.PROCESSED
    assert len([e for e in await service.outbox() if "AssetModerated" in e.event_type]) == 1


# ---------------------------------------------------------------- пакетная загрузка архивом
def _package_zip(entries: dict[str | zipfile.ZipInfo, bytes], manifest: Any) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for name, data in entries.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def _package_headers(
    service: Service, *perms: str, idem: str | None = None, admin: UUID | None = None
) -> dict[str, str]:
    """Заголовки загрузки; ``admin`` - один и тот же админ (по умолчанию токен с новым id)."""
    token = service.issuer.issue(user_id=admin, perms=perms or ADMIN_PERMS)
    return {
        "Authorization": f"Bearer {token}",
        "Idempotency-Key": idem or f"pkg-{uuid7().hex}",
        "Content-Type": "application/zip",
    }


async def _package_status(service: Service, package_id: str) -> dict[str, Any]:
    response = await service.client.get(
        f"/admin/v1/packages/{package_id}", headers=service.admin("admin.access")
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _archive_keys(service: Service) -> list[str]:
    """Ключи архивов пакетов в бакете исходников (после обработки их быть не должно)."""
    return [k for k in await service.source_keys() if k.startswith("k/")]


@pytest.mark.parametrize("service", [{"package_max_ratio": 10_000}], indirect=True)
async def test_package_upload_end_to_end(service: Service) -> None:
    event, item_id = _music_item_event()
    await service.deliver(event)
    manifest = {
        "version": 1,
        "items": [
            {
                "key": "track:fma-1",
                "kind": "track",
                "media_item_id": item_id,
                "files": [
                    {"role": "audio", "path": "audio/a.bin"},
                    {"role": "audio", "path": "audio/broken.m4a"},
                ],
                "license_note": "",
            }
        ],
    }
    archive = _package_zip(
        {"audio/a.bin": _wav(50), "audio/broken.m4a": b"not audio at all"}, manifest
    )
    headers = _package_headers(service)
    first = await service.client.post(
        "/admin/v1/packages", params={"reason": "пакет"}, content=archive, headers=headers
    )
    assert first.status_code == 202, first.text
    package_id = first.json()["package_id"]
    assert first.json()["status"] == "queued"
    again = await service.client.post(
        "/admin/v1/packages", params={"reason": "пакет"}, content=archive, headers=headers
    )
    assert (again.status_code, again.json()["package_id"]) == (202, package_id)

    assert (await _package_status(service, package_id))["status"] == "queued"
    await service.run_tasks()
    status = await _package_status(service, package_id)
    assert status["error_code"] is None, status
    assert (status["status"], status["total"], status["done"], status["failed"]) == (
        "partial",
        2,
        1,
        1,
    )
    assert (status["missing_source"], status["missing_license"]) == (1, 1)
    (error,) = status["errors"]
    assert (error["path"], error["role"], error["item_key"]) == (
        "audio/broken.m4a",
        "audio",
        "track:fma-1",
    )
    assert error["code"].startswith("source.")
    # исходник прошёл тот же приём, что и ручная загрузка: он виден в списке единицы
    listed = await service.client.get(
        "/admin/v1/sources",
        params={"media_item_id": item_id},
        headers=service.admin("admin.access"),
    )
    (source,) = [s for s in listed.json() if s["kind"] == "audio"]
    assert (source["origin"], source["license_note"], source["license_missing"]) == (
        "upload",
        None,
        True,
    )
    sets = (
        await service.client.get("/internal/v1/asset-sets", params={"media_item_id": item_id})
    ).json()
    assert sets and {s["status"] for s in sets} == {"ready"}
    assert await _archive_keys(service) == []


@pytest.mark.parametrize("service", [{"package_max_ratio": 10_000}], indirect=True)
async def test_package_resend_with_new_key_does_not_duplicate_sources(service: Service) -> None:
    event, item_id = _music_item_event()
    await service.deliver(event)
    manifest = {
        "version": 1,
        "items": [
            {
                "key": "track:fma-2",
                "kind": "track",
                "media_item_id": item_id,
                "files": [{"role": "audio", "path": "a.bin"}],
            }
        ],
    }
    archive = _package_zip({"a.bin": _wav(50)}, manifest)
    admin = uuid7()  # повтор того же пакета тем же админом (ключи идемпотентности - на актора)
    for _ in range(2):
        response = await service.client.post(
            "/admin/v1/packages",
            params={"reason": "пакет"},
            content=archive,
            headers=_package_headers(service, admin=admin),
        )
        assert response.status_code == 202
        await service.run_tasks()
        assert (await _package_status(service, response.json()["package_id"]))["done"] == 1
    listed = await service.client.get(
        "/admin/v1/sources",
        params={"media_item_id": item_id},
        headers=service.admin("admin.access"),
    )
    assert len([s for s in listed.json() if s["kind"] == "audio"]) == 1


async def test_malicious_package_is_rejected_by_worker_and_reported(service: Service) -> None:
    link = zipfile.ZipInfo("link.wav")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    manifest = {
        "version": 1,
        "items": [
            {"key": "track:evil", "kind": "track", "files": [{"role": "audio", "path": "link.wav"}]}
        ],
    }
    response = await service.client.post(
        "/admin/v1/packages",
        params={"reason": "пакет"},
        content=_package_zip({link: b"/etc/passwd"}, manifest),
        headers=_package_headers(service),
    )
    assert response.status_code == 202
    await service.run_tasks()
    status = await _package_status(service, response.json()["package_id"])
    assert (status["status"], status["error_code"]) == ("failed", "package.symlink")
    assert (status["done"], status["errors"]) == (0, [])
    assert await _archive_keys(service) == []


async def test_package_api_contract(service: Service) -> None:
    archive = _package_zip({}, {"version": 1, "items": []})
    post = service.client.post
    assert_problem(
        await post(
            "/admin/v1/packages",
            params={"reason": "причина"},
            content=archive,
            headers={"Idempotency-Key": "key-00001", "Content-Type": "application/zip"},
        ),
        401,
        "auth.unauthenticated",
    )
    assert_problem(  # без права загрузки - 403
        await post(
            "/admin/v1/packages",
            params={"reason": "причина"},
            content=archive,
            headers=_package_headers(service, "admin.access"),
        ),
        403,
        "auth.forbidden",
    )
    assert_problem(  # не zip
        await post(
            "/admin/v1/packages",
            params={"reason": "причина"},
            content=b"MZ not a zip",
            headers=_package_headers(service),
        ),
        422,
        "package.not_zip",
    )
    assert_problem(  # без Idempotency-Key
        await post(
            "/admin/v1/packages",
            params={"reason": "причина"},
            content=archive,
            headers={**service.admin(), "Content-Type": "application/zip"},
        ),
        422,
        "idempotency.key_required",
    )
    assert_problem(
        await service.client.get(
            f"/admin/v1/packages/{uuid7()}", headers=service.admin("admin.access")
        ),
        404,
        "package.not_found",
    )
    assert await _archive_keys(service) == []


@pytest.mark.parametrize("service", [{"package_max_archive_mb": 1}], indirect=True)
async def test_package_over_limit_is_413_and_leaves_nothing(service: Service) -> None:
    big = b"PK\x03\x04" + b"\x00" * (1024 * 1024 + 10)
    response = await service.client.post(
        "/admin/v1/packages",
        params={"reason": "причина"},
        content=big,
        headers=_package_headers(service),
    )
    assert response.status_code == 413
    assert response.json()["code"] == "package.archive_too_large"
    assert await _archive_keys(service) == []


async def test_admin_replan_static_profile_batches_and_checks_rights(service: Service) -> None:
    """Пересоздание наборов статического профиля: порция, курсор, права, ключ, ошибки."""
    asset_set = await _ready_poster(service)
    url = "/admin/v1/asset-sets/replan"
    body = {"clue_type": "poster_masked", "domain": "film", "limit": 10, "reason": "новый хэш"}
    headers = {**service.admin(), "Idempotency-Key": f"replan-{uuid7().hex}"}
    response = await service.client.post(url, json=body, headers=headers)
    assert response.status_code == 200, response.text
    # Набор под этот хэш уже есть: единица просмотрена, поставлять нечего, домен пройден.
    assert response.json() == {"processed": 1, "scheduled": 0, "next_after": None}
    assert (await service.client.post(url, json=body, headers=headers)).json() == response.json()
    assert_problem(
        await service.client.post(url, json={**body, "limit": 0}, headers=headers),
        422,
        "request.validation_failed",
    )
    assert_problem(
        await service.client.post(
            url,
            json={**body, "clue_type": "no_such_clue"},
            headers={**service.admin(), "Idempotency-Key": f"replan-{uuid7().hex}"},
        ),
        422,
        "profile.not_found",
    )
    assert_problem(
        await service.client.post(
            url,
            json=body,
            headers={**service.admin("admin.access"), "Idempotency-Key": f"r-{uuid7().hex}"},
        ),
        403,
        "auth.forbidden",
    )
    assert_problem(
        await service.client.post(url, json=body, headers=service.admin()),
        422,
        "idempotency.key_required",
    )
    assert asset_set["status"] == "ready"


async def test_admin_replan_schedules_missing_set_on_real_db(service: Service) -> None:
    """Набора под текущий хэш профиля нет (как после правки yaml) → replan ставит и строит его."""
    asset_set = await _ready_poster(service)
    sets = TABLES.metadata.tables["asset_sets"]
    assert service.infra.sessions is not None
    async with service.infra.sessions() as session:
        await session.execute(sa.delete(sets).where(sets.c.id == UUID(asset_set["asset_set_id"])))
        await session.commit()
    body = {"clue_type": "poster_masked", "domain": "film", "limit": 10, "reason": "новый хэш"}
    headers = {**service.admin(), "Idempotency-Key": f"replan-{uuid7().hex}"}
    response = await service.client.post("/admin/v1/asset-sets/replan", json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json() == {"processed": 1, "scheduled": 1, "next_after": None}
    assert await service.run_tasks() == 1
    listed = await service.client.get(
        "/internal/v1/asset-sets", params={"media_item_id": asset_set["media_item_id"]}
    )
    (rebuilt,) = listed.json()
    assert rebuilt["status"] == "ready"
    assert rebuilt["asset_set_id"] != asset_set["asset_set_id"]
