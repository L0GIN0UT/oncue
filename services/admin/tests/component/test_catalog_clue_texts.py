"""«Каталог», этап 3a: тексты загадок (правка, одобрение) и переопубликация единиц."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from admin_service.audit.models import AuditEntry

from gv_core.ids import uuid7

pytestmark = [pytest.mark.component, pytest.mark.django_db]

ITEM = uuid7()
TEXTS = f"/admin/v1/items/{ITEM}/clue-texts"
PAGE = f"/catalog/items/{ITEM}/clue-texts/"
ITEM_JSON = {
    "id": str(ITEM),
    "domain": "film",
    "kind": "film",
    "title": "Интерстеллар",
    "original_title": "Interstellar",
    "subtitle": None,
    "year": 2014,
    "status": "active",
    "blocked_reason_code": None,
    "version": 3,
    "aliases": [],
    "persons": [],
    "genres": [],
    "external_refs": {},
    "metrics": {},
    "source_media": [],
}


def _texts(status: str = "draft", revision: int = 2, **kw: Any) -> dict[str, Any]:
    return {
        "plot_raw": "Купер и экипаж летят через червоточину у Сатурна.",
        "plot_masked": "… и экипаж летят через червоточину у Сатурна.",
        "tagline": "Следующий шаг человечества",
        "emoji": "🚀🌌⏳",
        "status": status,
        "revision": revision,
        "approved_revision": 1,
    } | kw


def _key() -> str:
    return str(uuid.uuid4())


def _open(catalog: Any, texts: dict[str, Any] | None = None) -> None:
    catalog.route("GET", f"/admin/v1/items/{ITEM}").respond(200, json=ITEM_JSON)
    catalog.route("GET", TEXTS).respond(200, json=texts or _texts())


# ------------------------------------------------------------------------------ редактор


def test_editor_shows_raw_plot_next_to_masked_and_approve(
    client: Any, login: Any, catalog: Any
) -> None:
    login()
    _open(catalog)
    page = client.get(PAGE).content.decode()
    assert "Купер и экипаж" in page  # исходный сюжет — для сверки
    assert "… и экипаж летят" in page  # маскированный — в поле правки
    assert "Следующий шаг человечества" in page and "🚀🌌⏳" in page
    assert 'name="base_revision" value="2"' in page
    assert f'action="/catalog/items/{ITEM}/clue-texts/approve/"' in page
    assert "Одобрить r2" in page
    assert "одобрена r1" in page


def test_approved_texts_have_no_approve_button(client: Any, login: Any, catalog: Any) -> None:
    login()
    _open(catalog, _texts(status="approved", approved_revision=2))
    page = client.get(PAGE).content.decode()
    assert "/clue-texts/approve/" not in page
    assert "одобрены" in page


def test_save_sends_texts_revision_and_key_and_audits(
    client: Any, login: Any, catalog: Any
) -> None:
    login(perms=("admin.access", "catalog.edit_aliases"), roles=("moderator",))
    key = _key()
    route = catalog.route("PUT", TEXTS).respond(200, json=_texts(revision=3))
    response = client.post(
        f"/catalog/items/{ITEM}/clue-texts/save/",
        {
            "plot_masked": "… летит к Сатурну.",
            "tagline": "",
            "emoji": "🚀🪐",
            "base_revision": "2",
            "reason": "убрал имя героя",
            "idempotency_key": key,
        },
    )
    assert response.status_code == 302
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == key
    assert json.loads(request.content) == {
        "plot_masked": "… летит к Сатурну.",
        "tagline": None,  # пустое поле — очистить
        "emoji": "🚀🪐",
        "base_revision": 2,
        "reason": "убрал имя героя",
    }
    entry = AuditEntry.objects.get(action="catalog.edit_clue_texts")
    assert entry.before == {"revision": 2}
    assert entry.after == {"revision": 3, "status": "draft"}  # сами тексты в аудит не пишутся


def test_revision_conflict_keeps_input_and_audits_refusal(
    client: Any, login: Any, catalog: Any, problem: Any
) -> None:
    login()
    _open(catalog, _texts(revision=5))
    catalog.route("PUT", TEXTS).respond(
        409, json=problem(409, "catalog.clue_texts_revision_mismatch")
    )
    response = client.post(
        f"/catalog/items/{ITEM}/clue-texts/save/",
        {
            "plot_masked": "мой вариант сюжета",
            "base_revision": "2",
            "reason": "правка",
            "idempotency_key": _key(),
        },
    )
    page = response.content.decode()
    assert "уже изменил кто-то другой" in page
    assert "мой вариант сюжета" in page  # правка не потерялась
    entry = AuditEntry.objects.get(action="catalog.edit_clue_texts")
    assert entry.error_code == "catalog.clue_texts_revision_mismatch"


def test_invalid_emoji_is_explained(client: Any, login: Any, catalog: Any, problem: Any) -> None:
    login()
    _open(catalog)
    catalog.route("PUT", TEXTS).respond(422, json=problem(422, "catalog.clue_text_invalid_emoji"))
    response = client.post(
        f"/catalog/items/{ITEM}/clue-texts/save/",
        {"emoji": "🚀 🚀", "base_revision": "2", "reason": "эмодзи", "idempotency_key": _key()},
    )
    assert "Эмодзи: без пробелов и не больше 12 знаков" in response.content.decode()
    assert AuditEntry.objects.get().error_code == "catalog.clue_text_invalid_emoji"


def test_too_long_emoji_is_form_error_without_call(client: Any, login: Any, catalog: Any) -> None:
    login()
    _open(catalog)
    route = catalog.route("PUT", TEXTS).respond(200, json=_texts())
    response = client.post(
        f"/catalog/items/{ITEM}/clue-texts/save/",
        {"emoji": "🚀" * 65, "base_revision": "2", "reason": "эмодзи", "idempotency_key": _key()},
    )
    assert response.status_code == 400
    assert not route.called


def test_approve_sends_revision_and_audits(client: Any, login: Any, catalog: Any) -> None:
    login()
    key = _key()
    route = catalog.route("POST", f"{TEXTS}/approve").respond(
        200, json=_texts(status="approved", approved_revision=2)
    )
    response = client.post(
        f"/catalog/items/{ITEM}/clue-texts/approve/",
        {"revision": "2", "reason": "проверено", "idempotency_key": key},
    )
    assert response.status_code == 302
    assert route.calls.last.request.headers["Idempotency-Key"] == key
    assert json.loads(route.calls.last.request.content) == {"revision": 2, "reason": "проверено"}
    entry = AuditEntry.objects.get(action="catalog.approve_clue_texts")
    assert entry.after == {"revision": 2, "status": "approved", "approved_revision": 2}


@pytest.mark.parametrize(
    ("status", "code", "text"),
    [
        (409, "catalog.clue_texts_revision_mismatch", "уже изменил кто-то другой"),
        (422, "catalog.clue_texts_empty", "Одобрять нечего"),
    ],
)
def test_approve_refusal_is_explained_and_audited(  # noqa: PLR0917 — фикстуры и параметры pytest
    client: Any, login: Any, catalog: Any, problem: Any, status: int, code: str, text: str
) -> None:
    login()
    _open(catalog)
    catalog.route("POST", f"{TEXTS}/approve").respond(status, json=problem(status, code))
    response = client.post(
        f"/catalog/items/{ITEM}/clue-texts/approve/",
        {"revision": "2", "reason": "проверено", "idempotency_key": _key()},
        follow=True,
    )
    assert text in response.content.decode()
    assert AuditEntry.objects.get(action="catalog.approve_clue_texts").error_code == code


def test_without_edit_perm_texts_are_read_only(client: Any, login: Any, catalog: Any) -> None:
    login(perms=("admin.access",), roles=("support",))
    _open(catalog)
    save = catalog.route("PUT", TEXTS).respond(200, json=_texts())
    approve = catalog.route("POST", f"{TEXTS}/approve").respond(200, json=_texts())
    page = client.get(PAGE).content.decode()
    assert "Купер и экипаж" in page
    assert "Сохранить тексты" not in page and "/clue-texts/approve/" not in page
    data = {"revision": "2", "base_revision": "2", "reason": "xxx", "idempotency_key": _key()}
    assert client.post(f"/catalog/items/{ITEM}/clue-texts/save/", data).status_code == 403
    assert client.post(f"/catalog/items/{ITEM}/clue-texts/approve/", data).status_code == 403
    assert not save.called and not approve.called
    assert not AuditEntry.objects.exists()


# ------------------------------------------------------------------------------ переопубликация


def test_republish_requires_confirmation(client: Any, login: Any, catalog: Any) -> None:
    login()
    route = catalog.route("POST", "/admin/v1/items/republish").respond(
        202, json={"job_id": str(uuid7())}
    )
    response = client.post(
        "/catalog/republish/", {"domain": "film", "reason": "этап 3a", "idempotency_key": _key()}
    )
    assert response.status_code == 400
    assert "подтвердите переопубликацию" in response.content.decode()
    assert not route.called


def test_republish_sends_domain_reason_key_and_audits(
    client: Any, login: Any, catalog: Any
) -> None:
    login()
    key, job = _key(), str(uuid7())
    route = catalog.route("POST", "/admin/v1/items/republish").respond(202, json={"job_id": job})
    response = client.post(
        "/catalog/republish/",
        {"domain": "film", "confirm": "on", "reason": "этап 3a", "idempotency_key": key},
        follow=True,
    )
    assert job in response.content.decode()
    request = route.calls.last.request
    assert request.headers["Idempotency-Key"] == key
    assert json.loads(request.content) == {"domain": "film", "reason": "этап 3a"}
    entry = AuditEntry.objects.get(action="catalog.republish")
    assert (entry.target_type, entry.target_id) == ("catalog", "film")
    assert entry.after == {"job_id": job}


def test_republish_all_domains_and_refusal_is_audited(
    client: Any, login: Any, catalog: Any, problem: Any
) -> None:
    login()
    route = catalog.route("POST", "/admin/v1/items/republish").respond(
        503, json=problem(503, "catalog.tasks_unavailable")
    )
    response = client.post(
        "/catalog/republish/",
        {"domain": "", "confirm": "on", "reason": "этап 3a", "idempotency_key": _key()},
    )
    assert "Очередь задач catalog недоступна" in response.content.decode()
    assert json.loads(route.calls.last.request.content) == {"domain": None, "reason": "этап 3a"}
    entry = AuditEntry.objects.get(action="catalog.republish")
    assert (entry.target_id, entry.error_code) == ("all", "catalog.tasks_unavailable")


def test_republish_without_import_perm_is_hidden_and_forbidden(
    client: Any, login: Any, moderator_perms: tuple[str, ...]
) -> None:
    login(perms=moderator_perms, roles=("moderator",))
    assert client.get("/catalog/republish/").status_code == 403
    assert "/catalog/republish/" not in client.get("/").content.decode()


def test_menu_shows_republish_with_import_perm(client: Any, login: Any) -> None:
    login()
    assert 'href="/catalog/republish/"' in client.get("/").content.decode()
