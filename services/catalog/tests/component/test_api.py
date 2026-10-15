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
from catalog_service.application.commands.republish import REPUBLISH_TASK
from catalog_service.infrastructure.clients.resilience import CircuitBreaker, ResilientHttp
from catalog_service.infrastructure.clients.tmdb import TmdbClient
from catalog_service.infrastructure.container import CatalogContainer
from catalog_service.infrastructure.messaging import build_event_handlers, build_task_handlers
from catalog_service.main import DEFINITION
from catalog_service.settings import Settings

from gv_core.ids import uuid7
from gv_messaging import EventEnvelope, EventProcessor, Inbox
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


async def _first_film(
    client: httpx.AsyncClient, infra: Infrastructure, issuer: TestTokenIssuer
) -> str:
    await run_import(
        client, infra, issuer, {"source": "file", "file_id": "films.jsonl", "reason": "сиды"}
    )
    listed = await client.get(
        "/admin/v1/items",
        params={"domain": "film", "limit": 1},
        headers=auth(issuer, "admin.access"),
    )
    item_id: str = listed.json()["items"][0]["id"]
    return item_id


async def _outbox(infra: Infrastructure) -> list[EventEnvelope]:
    assert infra.sessions is not None and infra.tables is not None
    async with infra.sessions() as session:
        return await outbox_envelopes(session, infra.tables.outbox)


async def test_clue_texts_edit_approve_publishes_only_approved(
    client: httpx.AsyncClient, catalog_infra: Infrastructure, token_issuer: TestTokenIssuer
) -> None:
    """M9: правка → черновик (без события), одобрение → MediaItemUpdated.clue_texts."""
    item_id = await _first_film(client, catalog_infra, token_issuer)
    url = f"/admin/v1/items/{item_id}/clue-texts"
    editor = auth(token_issuer, "admin.access", "catalog.edit_aliases")

    empty = await client.get(url, headers=auth(token_issuer, "admin.access"))
    assert empty.status_code == 200, empty.text
    assert empty.json() == {
        "plot_raw": None, "plot_masked": None, "tagline": None, "emoji": None,
        "status": "draft", "revision": 0, "approved_revision": None,
    }  # fmt: skip

    body = {"plot_masked": "Сюжет …", "tagline": "Слоган", "emoji": "🚀🌌", "base_revision": 0}
    events_before = len(await _outbox(catalog_infra))
    put_key = key()
    edited = await client.put(url, json={**body, "reason": "черновик"}, headers=editor | put_key)
    assert edited.status_code == 200, edited.text
    assert (edited.json()["status"], edited.json()["revision"]) == ("draft", 1)
    replay = await client.put(url, json={**body, "reason": "черновик"}, headers=editor | put_key)
    assert replay.json() == edited.json()
    assert len(await _outbox(catalog_infra)) == events_before  # черновик не публикуется

    assert_problem(
        await client.put(url, json={**body, "reason": "устарело"}, headers=editor | key()),
        409,
        "catalog.clue_texts_revision_mismatch",
    )
    assert_problem(
        await client.put(
            url,
            json={**body, "base_revision": 1, "emoji": "🚀" * 13, "reason": "много"},
            headers=editor | key(),
        ),
        422,
        "catalog.clue_text_invalid_emoji",
    )
    approve = f"{url}/approve"
    assert_problem(
        await client.post(approve, json={"revision": 2, "reason": "не та"}, headers=editor | key()),
        409,
        "catalog.clue_texts_revision_mismatch",
    )
    approved = await client.post(
        approve, json={"revision": 1, "reason": "проверено"}, headers=editor | key()
    )
    assert approved.status_code == 200, approved.text
    assert (approved.json()["status"], approved.json()["approved_revision"]) == ("approved", 1)

    last = (await _outbox(catalog_infra))[-1]
    assert_event_matches_schema(last)
    assert last.event_type == "catalog.MediaItemUpdated"
    assert last.payload["clue_texts"] == {
        "plot_masked": "Сюжет …", "tagline": "Слоган", "emoji": "🚀🌌",
    }  # fmt: skip
    assert last.payload["changed_fields"] == ["clue_texts"]


async def test_clue_texts_permissions_and_missing_item(
    client: httpx.AsyncClient, catalog_infra: Infrastructure, token_issuer: TestTokenIssuer
) -> None:
    item_id = await _first_film(client, catalog_infra, token_issuer)
    url = f"/admin/v1/items/{item_id}/clue-texts"
    body = {"plot_masked": "x", "base_revision": 0, "reason": "без права"}
    assert_problem(await client.get(url), 401, "auth.unauthenticated")
    for perms in (("admin.access",), ("catalog.edit_aliases",), ("admin.access", "catalog.import")):
        assert_problem(
            await client.put(url, json=body, headers=auth(token_issuer, *perms) | key()),
            403,
            "auth.forbidden",
        )
    editor = auth(token_issuer, "admin.access", "catalog.edit_aliases")
    assert_problem(
        await client.put(url, json=body, headers=editor), 422, "idempotency.key_required"
    )
    assert_problem(
        await client.get(f"/admin/v1/items/{uuid7()}/clue-texts", headers=editor),
        404,
        "catalog.item_not_found",
    )
    assert_problem(
        await client.post(
            f"{url}/approve", json={"revision": 0, "reason": "пусто"}, headers=editor | key()
        ),
        422,
        "request.validation_failed",
    )


async def test_republish_publishes_version_plus_one_in_batches(
    client: httpx.AsyncClient,
    catalog_infra: Infrastructure,
    container: CatalogContainer,
    token_issuer: TestTokenIssuer,
) -> None:
    await _first_film(client, catalog_infra, token_issuer)
    importer = auth(token_issuer, "admin.access", "catalog.import")
    assert_problem(
        await client.post(
            "/admin/v1/items/republish",
            json={"domain": "film", "reason": "без права"},
            headers=auth(token_issuer, "admin.access", "catalog.edit_aliases") | key(),
        ),
        403,
        "auth.forbidden",
    )
    before = {e.aggregate_id: e.payload["version"] for e in await _outbox(catalog_infra)}
    queue = container.tasks
    assert isinstance(queue, InMemoryTaskQueue)
    queue.tasks.clear()
    republish_key = key()
    body = {"domain": "film", "reason": "заполнить новые поля"}
    response = await client.post(
        "/admin/v1/items/republish", json=body, headers=importer | republish_key
    )
    assert response.status_code == 202, response.text
    replay = await client.post(
        "/admin/v1/items/republish", json=body, headers=importer | republish_key
    )
    assert replay.json() == response.json()

    handler = build_task_handlers(catalog_infra)[REPUBLISH_TASK]
    batches = 0
    while queue.tasks:
        task = queue.tasks.pop(0)
        await handler(task["payload"])
        batches += 1
    assert batches == 1  # 22 фильма < порции по умолчанию (200)

    republished = [
        e for e in await _outbox(catalog_infra) if e.payload.get("changed_fields") == ["republish"]
    ]
    assert len(republished) == 22
    for event in republished:
        assert_event_matches_schema(event)
        assert event.payload["version"] == before[event.aggregate_id] + 1
