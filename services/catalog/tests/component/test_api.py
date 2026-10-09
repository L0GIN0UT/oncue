"""catalog целиком через HTTP: импорт → события → автокомплит, блокировка, права, лимиты.

OpenSearch не настроен — автокомплит и поиск работают на trigram-fallback (так же сервис
деградирует в проде при недоступном OpenSearch).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import respx
from catalog_service.application.commands.imports import IMPORT_TASK
from catalog_service.infrastructure.clients.resilience import CircuitBreaker, ResilientHttp
from catalog_service.infrastructure.clients.tmdb import TmdbClient
from catalog_service.infrastructure.container import CatalogContainer
from catalog_service.infrastructure.messaging import build_event_handlers, build_task_handlers
from catalog_service.main import DEFINITION
from catalog_service.settings import Settings

from gv_core.ids import uuid7
from gv_messaging import EventProcessor, Inbox
from gv_service import Infrastructure, create_app
from gv_testkit import (
    FakeClock,
    FakeDeadLetterSink,
    InMemoryTaskQueue,
    TestTokenIssuer,
    assert_event_matches_schema,
    assert_problem,
    outbox_envelopes,
    service_client,
)

pytestmark = pytest.mark.component

ADMIN_PERMS = ("admin.access", "catalog.import", "catalog.block_item", "catalog.edit_aliases")
FIXTURES = Path(__file__).parent.parent / "fixtures" / "external"


@pytest.fixture
async def client(
    catalog_infra: Infrastructure, container: CatalogContainer, token_issuer: TestTokenIssuer
) -> AsyncIterator[httpx.AsyncClient]:
    # Подмена cached_property фейком того же протокола: mypy ждёт LazyTaskQueue.
    container.tasks = InMemoryTaskQueue()  # type: ignore[assignment]
    app = create_app(DEFINITION, catalog_infra, jwt_verifier=token_issuer.verifier())
    async with service_client(app) as c:
        yield c


def auth(issuer: TestTokenIssuer, *perms: str, user: uuid.UUID | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {issuer.issue(perms=perms, user_id=user)}"}


def key() -> dict[str, str]:
    return {"Idempotency-Key": f"k-{uuid7().hex}"}


async def run_import(
    client: httpx.AsyncClient, infra: Infrastructure, issuer: TestTokenIssuer, body: dict[str, str]
) -> dict[str, object]:
    headers = auth(issuer, *ADMIN_PERMS) | key()
    response = await client.post("/admin/v1/imports", json=body, headers=headers)
    assert response.status_code == 202, response.text
    import_id = response.json()["import_id"]
    await build_task_handlers(infra)[IMPORT_TASK]({"import_id": import_id})
    status = await client.get(f"/admin/v1/imports/{import_id}", headers=headers)
    assert status.status_code == 200
    result: dict[str, object] = status.json()
    return result


async def consume_own_events(infra: Infrastructure) -> int:
    assert infra.sessions is not None and infra.tables is not None
    processor = EventProcessor(
        group="catalog.consumer",
        registry=build_event_handlers(infra),
        session_factory=infra.sessions,
        inbox=Inbox(infra.tables.inbox),
        dead_letters=FakeDeadLetterSink(),
        clock=FakeClock(),
    )
    async with infra.sessions() as session:
        envelopes = await outbox_envelopes(session, infra.tables.outbox)
    for envelope in envelopes:
        assert_event_matches_schema(envelope)
        await processor.process(envelope.to_bytes(), source="test")
    return len(envelopes)


async def autocomplete(client: httpx.AsyncClient, q: str, **params: str) -> list[str]:
    response = await client.get("/v1/autocomplete", params={"q": q, **params})
    assert response.status_code == 200, response.text
    return [i["title"] for i in response.json()["items"]]


# ------------------------------------------------------------------ сценарии


async def test_file_import_events_and_autocomplete(
    client: httpx.AsyncClient,
    catalog_infra: Infrastructure,
    container: CatalogContainer,
    token_issuer: TestTokenIssuer,
) -> None:
    films = await run_import(
        client,
        catalog_infra,
        token_issuer,
        {"source": "file", "file_id": "films.jsonl", "reason": "сиды"},
    )
    tracks = await run_import(
        client,
        catalog_infra,
        token_issuer,
        {"source": "file", "file_id": "tracks.jsonl", "reason": "сиды"},
    )
    assert films["status"] == tracks["status"] == "done"
    assert films["counters"] == {"created": 22, "updated": 0, "unchanged": 0, "failed": 0}
    assert isinstance(container.tasks, InMemoryTaskQueue)
    assert container.tasks.tasks  # задача поставлена в очередь

    assert await consume_own_events(catalog_infra) == 44

    assert "Интерстеллар" in await autocomplete(client, "инте")
    assert await autocomplete(client, "Ёжик") == []
    assert (await autocomplete(client, "interstellar"))[0] == "Интерстеллар"
    assert await autocomplete(client, "группа кр", domain="music") == ["Группа крови"]

    search = await client.get("/v1/search", params={"q": "шоушенк"})
    assert [i["title"] for i in search.json()["items"]] == ["Побег из Шоушенка"]

    # Повторный импорт того же файла ничего не меняет.
    again = await run_import(
        client,
        catalog_infra,
        token_issuer,
        {"source": "file", "file_id": "films.jsonl", "reason": "повтор"},
    )
    assert again["counters"] == {"created": 0, "updated": 0, "unchanged": 22, "failed": 0}


async def test_block_hides_item_everywhere(
    client: httpx.AsyncClient, catalog_infra: Infrastructure, token_issuer: TestTokenIssuer
) -> None:
    await run_import(
        client,
        catalog_infra,
        token_issuer,
        {"source": "file", "file_id": "films.jsonl", "reason": "сиды"},
    )
    admin = auth(token_issuer, *ADMIN_PERMS)
    page = (await client.get("/admin/v1/items", params={"q": "интерстеллар"}, headers=admin)).json()
    [row] = page["items"]
    item_id = row["id"]
    assert (await client.get(f"/v1/items/{item_id}")).json()["title"] == "Интерстеллар"
    assert "Интерстеллар" in await autocomplete(client, "интерс")  # заполнили кэш

    headers = admin | key()
    body = {"reason": "жалоба правообладателя", "reason_code": "copyright.claim"}
    blocked = await client.post(f"/admin/v1/items/{item_id}/block", json=body, headers=headers)
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["status"] == "blocked"
    replay = await client.post(f"/admin/v1/items/{item_id}/block", json=body, headers=headers)
    assert replay.json() == blocked.json()
    reused = await client.post(
        f"/admin/v1/items/{item_id}/unblock", json={"reason": "ошибка"}, headers=headers
    )
    assert_problem(reused, 409, "idempotency.key_reused")
    again = await client.post(f"/admin/v1/items/{item_id}/block", json=body, headers=admin | key())
    assert_problem(again, 409, "catalog.item_already_blocked")

    assert "Интерстеллар" not in await autocomplete(client, "интерс")
    assert_problem(await client.get(f"/v1/items/{item_id}"), 404, "catalog.item_not_found")
    admin_view = await client.get(f"/admin/v1/items/{item_id}", headers=admin)
    assert admin_view.json()["blocked_reason_code"] == "copyright.claim"

    assert catalog_infra.sessions is not None and catalog_infra.tables is not None
    async with catalog_infra.sessions() as session:
        events = await outbox_envelopes(session, catalog_infra.tables.outbox)
    [blocked_event] = [e for e in events if e.event_type == "catalog.MediaItemBlocked"]
    assert_event_matches_schema(blocked_event)
    assert blocked_event.payload["reason_code"] == "copyright.claim"

    unblocked = await client.post(
        f"/admin/v1/items/{item_id}/unblock", json={"reason": "отозвано"}, headers=admin | key()
    )
    assert unblocked.json()["status"] == "active"
    assert "Интерстеллар" in await autocomplete(client, "интерс")


async def test_replace_aliases(
    client: httpx.AsyncClient, catalog_infra: Infrastructure, token_issuer: TestTokenIssuer
) -> None:
    await run_import(
        client,
        catalog_infra,
        token_issuer,
        {"source": "file", "file_id": "films.jsonl", "reason": "сиды"},
    )
    admin = auth(token_issuer, *ADMIN_PERMS)
    [row] = (await client.get("/admin/v1/items", params={"q": "матрица"}, headers=admin)).json()[
        "items"
    ]
    body = {
        "aliases": [
            {"text": "Матрица", "kind": "title", "lang": "ru"},
            {"text": "Матрёшка", "kind": "short"},
        ],
        "reason": "народное название",
    }
    response = await client.put(
        f"/admin/v1/items/{row['id']}/aliases", json=body, headers=admin | key()
    )
    assert response.status_code == 200, response.text
    sources = {a["normalized"]: a["source"] for a in response.json()["aliases"]}
    assert sources["матрешка"] == "manual"
    assert "Матрица" in await autocomplete(client, "матреш")

    no_title = {"aliases": [{"text": "x", "kind": "short"}], "reason": "тест"}
    bad = await client.put(
        f"/admin/v1/items/{row['id']}/aliases", json=no_title, headers=admin | key()
    )
    assert_problem(bad, 422, "catalog.title_alias_required")


async def test_admin_api_requires_perms_and_idempotency_key(
    client: httpx.AsyncClient, token_issuer: TestTokenIssuer
) -> None:
    item = uuid7()
    body = {"reason": "проверка"}
    assert_problem(await client.get("/admin/v1/items"), 401, "auth.unauthenticated")
    viewer = auth(token_issuer, "admin.access")
    assert (await client.get("/admin/v1/items", headers=viewer)).status_code == 200
    forbidden = await client.post(
        f"/admin/v1/items/{item}/block", json=body, headers=viewer | key()
    )
    assert_problem(forbidden, 403, "auth.forbidden")
    no_access = auth(token_issuer, "catalog.block_item")
    assert_problem(
        await client.post(f"/admin/v1/items/{item}/block", json=body, headers=no_access | key()),
        403,
        "auth.forbidden",
    )
    moderator = auth(token_issuer, "admin.access", "catalog.block_item")
    no_key = await client.post(f"/admin/v1/items/{item}/block", json=body, headers=moderator)
    assert_problem(no_key, 422, "idempotency.key_required")
    bad_key = await client.post(
        f"/admin/v1/items/{item}/block", json=body, headers=moderator | {"Idempotency-Key": "x"}
    )
    assert_problem(bad_key, 422, "idempotency.key_invalid")
    blank_reason = await client.post(
        f"/admin/v1/items/{item}/block", json={"reason": "   "}, headers=moderator | key()
    )
    assert_problem(blank_reason, 422, "request.validation_failed")
    no_reason = await client.post(
        f"/admin/v1/items/{item}/block", json={}, headers=moderator | key()
    )
    assert_problem(no_reason, 422, "request.validation_failed")
    missing = await client.post(
        f"/admin/v1/items/{item}/block", json=body, headers=moderator | key()
    )
    assert_problem(missing, 404, "catalog.item_not_found")
    imports = {"source": "file", "file_id": "films.jsonl", "reason": "x" * 3}
    assert_problem(
        await client.post("/admin/v1/imports", json=imports, headers=moderator | key()),
        403,
        "auth.forbidden",
    )


async def test_import_validation_and_disabled_sources(
    client: httpx.AsyncClient, token_issuer: TestTokenIssuer
) -> None:
    admin = auth(token_issuer, *ADMIN_PERMS)
    disabled = {"source": "tmdb", "ref": "157336", "reason": "демо"}
    assert_problem(
        await client.post("/admin/v1/imports", json=disabled, headers=admin | key()),
        403,
        "catalog.source_disabled",
    )
    traversal = {"source": "file", "file_id": "../../etc/passwd", "reason": "x" * 3}
    assert_problem(
        await client.post("/admin/v1/imports", json=traversal, headers=admin | key()),
        422,
        "catalog.import_bad_ref",
    )
    assert_problem(
        await client.get(f"/admin/v1/imports/{uuid7()}", headers=admin),
        404,
        "catalog.import_not_found",
    )


async def test_tmdb_import_through_admin_api(
    client: httpx.AsyncClient,
    catalog_infra: Infrastructure,
    container: CatalogContainer,
    token_issuer: TestTokenIssuer,
) -> None:
    # Включаем TMDB для этого теста: внешний API — заглушка на записанном ответе. Реестр
    # источников собирается лениво, при первом импорте — уже с этим провайдером.
    container.film_provider = TmdbClient(
        ResilientHttp(
            "tmdb",
            httpx.AsyncClient(base_url="https://api.themoviedb.org/3"),
            rps=100,
            breaker=CircuitBreaker("tmdb", 3, 30),
        )
    )
    payload = json.loads((FIXTURES / "tmdb_movie_157336.json").read_text("utf-8"))
    with respx.mock(assert_all_called=True) as mock:
        mock.get("https://api.themoviedb.org/3/movie/157336").respond(200, json=payload)
        result = await run_import(
            client,
            catalog_infra,
            token_issuer,
            {"source": "tmdb", "ref": "157336", "reason": "демо"},
        )
    assert result["status"] == "done"
    assert await consume_own_events(catalog_infra) == 1
    assert await autocomplete(client, "инте") == ["Интерстеллар"]


async def test_public_query_limits(client: httpx.AsyncClient) -> None:
    assert_problem(
        await client.get("/v1/autocomplete", params={"q": "x" * 65}), 422, "catalog.query_too_long"
    )
    assert (await client.get("/v1/autocomplete", params={"q": "x" * 300})).status_code == 422
    assert (await client.get("/v1/autocomplete", params={"q": "и"})).json() == {"items": []}
    assert (await client.get("/v1/autocomplete", params={"q": "ин", "limit": 0})).status_code == 422
    assert (await client.get("/v1/meta")).json() == {"service": "catalog", "status": "ok"}
    assert (await client.get("/health/ready")).status_code == 200


async def test_autocomplete_rate_limited(
    database_url: str, catalog_infra: Infrastructure, token_issuer: TestTokenIssuer
) -> None:
    settings = catalog_infra.settings
    assert isinstance(settings, Settings)
    strict = Settings(
        database_url=database_url,
        redis_url=settings.redis_url,
        autocomplete=settings.autocomplete.model_copy(update={"rate_limit_per_minute": 2}),
    )
    infra = await Infrastructure.open(strict, DEFINITION.tables)
    try:
        app = create_app(DEFINITION, infra, jwt_verifier=token_issuer.verifier())
        async with service_client(app) as c:
            codes = [
                (await c.get("/v1/autocomplete", params={"q": "ин"})).status_code for _ in range(3)
            ]
            limited = await c.get("/v1/autocomplete", params={"q": "ин"})
        assert codes == [200, 200, 429]
        assert_problem(limited, 429, "rate.limited")
        assert limited.headers["Retry-After"] == "1"
    finally:
        await infra.close()
