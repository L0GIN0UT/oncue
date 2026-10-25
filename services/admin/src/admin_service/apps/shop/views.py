"""Представления раздела «Магазин»: предметы, картинки, паки и партии промокодов.

Данные хранит progression (ADR 0042, 0048); админка работает только через его admin-API
(``core.progression_client``). Права: ``progression.items.manage`` — предметы и паки,
``progression.promo.manage`` — промокоды; сервис проверяет их ещё раз. Каждая мутация — форма
с обязательной причиной и ``Idempotency-Key`` из скрытого поля (``MutationForm``) + аудит
(``core.views.mutate``).

Коды промокодов сервис отдаёт один раз: они живут только в теле ответа на POST создания.
Не пишутся ни в сессию, ни в БД, ни в аудит, ни в логи; страница с кодами — ``no-store``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import quote
from uuid import UUID

from django.conf import settings
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from admin_service.apps.shop.forms import ItemForm, ItemMediaForm, PackForm, PromoForm
from admin_service.core.forms import MutationForm
from admin_service.core.payload import as_dict, as_list, as_str_list
from admin_service.core.permissions import require_perm
from admin_service.core.progression_client import PROGRESSION, ProgressionAdminClient
from admin_service.core.service_client import ServiceClient, ServiceError
from admin_service.core.views import error_status, mutate, read, service_error_response

ITEMS_LOAD_FAILED = "Не удалось загрузить список предметов"

ITEMS = "progression.items.manage"
PROMO = "progression.promo.manage"
_SCAN_PAGES = 20  # выбор предметов в паках и промокодах листает не больше 20×100
_PAGE = 100
_ITEM_FIELDS = (
    "slot",
    "rarity",
    "title",
    "media_kind",
    "level_required",
    "price",
    "active",
)
_ITEM_AUDIT = ("id", *_ITEM_FIELDS)


def _client(call: Callable[[ProgressionAdminClient], Any]) -> Callable[[ServiceClient], Any]:
    return lambda c: call(ProgressionAdminClient(c))


# ---------------------------------------------------------------------------- конфиг магазина


class ShopSettings:
    """Шапка действующего конфига progression: ``shop.slots``, ``rarities``, ``media``.

    Недоступный конфиг — пустые списки и ``media`` без лимитов: формы падают на текстовые
    поля, а проверку выполняет сервис.
    """

    def __init__(self, shop: dict[str, Any] | None, error: str | None = None) -> None:
        shop = shop or {}
        self.error = error
        self.slots = [
            (str(s["key"]), f"{s.get('title', s['key'])} ({s['key']})")
            for s in as_list(shop.get("slots"))
            if "key" in s
        ]
        self.rarities = [
            (str(s["key"]), f"{s.get('title', s['key'])} ({s['key']})")
            for s in as_list(shop.get("rarities"))
            if "key" in s
        ]
        self.media = as_dict(shop.get("media"))

    def media_limit(self, media_kind: str) -> int:
        """Предел файла (байт) для вида картинки: из конфига, не выше жёсткого потолка."""
        hard = int(settings.GV_SHOP_MEDIA_MAX_BYTES)
        raw = self.media.get(
            "animated_max_bytes" if media_kind == "animated" else "static_max_bytes"
        )
        if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
            return min(raw, hard)
        return hard


def shop_settings(request: HttpRequest) -> ShopSettings:
    """Действующий конфиг (опубликованная версия) через admin-API progression."""
    data, error = read(request, PROGRESSION, _client(lambda c: c.versions()))
    if error is not None:
        return ShopSettings(None, error.message)
    published = next(
        (c for c in as_list(as_dict(data).get("items")) if c.get("status") == "published"), None
    )
    version = as_dict(published).get("version")
    if not isinstance(version, int):
        return ShopSettings(None, "Действующий конфиг progression не найден")
    body, error = read(request, PROGRESSION, _client(lambda c: c.version(version)))
    if error is not None:
        return ShopSettings(None, error.message)
    return ShopSettings(as_dict(as_dict(as_dict(body).get("config"))).get("shop"))


def _items_page(
    request: HttpRequest, cursor: str | None, filters: dict[str, Any]
) -> tuple[Any, ServiceError | None]:
    def call(client: ProgressionAdminClient) -> Any:
        return client.items(cursor=cursor, limit=_PAGE, **filters)

    return read(request, PROGRESSION, _client(call))


def _item_pages(
    request: HttpRequest, **filters: Any
) -> Iterator[tuple[list[dict[str, Any]], ServiceError | None]]:
    """Страницы списка предметов (не больше ``_SCAN_PAGES``); после ошибки — конец."""
    cursor: str | None = None
    for _ in range(_SCAN_PAGES):
        data, error = _items_page(request, cursor, filters)
        if error is not None:
            yield [], error
            return
        yield as_list(as_dict(data).get("items")), None
        cursor = as_dict(data).get("next_cursor")
        if not cursor:
            return


def _scan_items(
    request: HttpRequest, **filters: Any
) -> tuple[list[dict[str, Any]], ServiceError | None]:
    """Все предметы (с ограничением по числу страниц) — для выбора в паках и промокодах."""
    rows: list[dict[str, Any]] = []
    for page, error in _item_pages(request, **filters):
        if error is not None:
            return [], error
        rows.extend(page)
    return rows, None


def _find_item(
    request: HttpRequest, item_id: UUID
) -> tuple[dict[str, Any] | None, ServiceError | None]:
    """Предмет по id: ``GET /admin/v1/items/{id}``.

    ``(None, None)`` — предмета нет (404); ``(None, ошибка)`` — сервис не ответил.
    """
    data, error = read(request, PROGRESSION, _client(lambda c: c.item(item_id)))
    if error is not None:
        return None, (None if error.status == 404 else error)  # noqa: PLR2004
    return as_dict(data), None


def _item_choices(rows: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(str(r["id"]), f"{r.get('title')} ({r.get('key')})") for r in rows if r.get("id")]


def _item_initial(item: dict[str, Any]) -> dict[str, Any]:
    return {"key": item.get("key"), **{f: item.get(f) for f in _ITEM_FIELDS}}


def _item_form(shop: ShopSettings, *args: Any, editing: bool = False, **kwargs: Any) -> ItemForm:
    return ItemForm(*args, slots=shop.slots, rarities=shop.rarities, editing=editing, **kwargs)


# ---------------------------------------------------------------------------- предметы


@require_GET
@require_perm(ITEMS)
def items(request: HttpRequest) -> HttpResponse:
    """Список предметов (фильтры ``slot``, ``active``, курсор)."""
    slot = request.GET.get("slot") or None
    active_raw = request.GET.get("active")
    active = {"1": True, "0": False}.get(active_raw or "")
    cursor = request.GET.get("cursor") or None
    data, error = read(
        request,
        PROGRESSION,
        _client(lambda c: c.items(slot=slot, active=active, cursor=cursor)),
    )
    shop = shop_settings(request)
    return render(
        request,
        "admin_shop/items.html",
        {
            "title": "Магазин: предметы",
            "error": error.message if error else None,
            "rows": as_list(as_dict(data).get("items")),
            "next_cursor": as_dict(data).get("next_cursor"),
            "slots": shop.slots,
            "slot": slot or "",
            "active": active_raw or "",
        },
    )


@require_http_methods(["GET", "POST"])
@require_perm(ITEMS)
def item_new(request: HttpRequest) -> HttpResponse:
    """Создание предмета (неактивным; картинка — после, на странице предмета)."""
    if request.method == "GET":
        shop = shop_settings(request)
        form = _item_form(shop)
        return render(
            request, "admin_shop/item_new.html", _form_context("Новый предмет", form, shop)
        )
    # При POST конфиг не запрашиваем: слот и редкость проверяет сервис (422 с кодом).
    shop = ShopSettings(None)
    form = _item_form(shop, request.POST)
    if not form.is_valid():
        context = _form_context("Новый предмет", form, shop)
        return render(request, "admin_shop/item_new.html", context, status=400)
    body, idem = form.payload(), str(form.cleaned_data["idempotency_key"])
    ok, result = mutate(
        request,
        service=PROGRESSION,
        action="shop.create_item",
        target_type="item",
        target_id=body["key"],
        reason=body["reason"],
        idempotency_key=idem,
        audit_fields=_ITEM_AUDIT,
        call=_client(lambda c: c.create_item(body, idempotency_key=idem)),
        success="Предмет создан",
    )
    item_id = as_dict(result).get("id")
    if ok and item_id:
        return redirect("shop:item_edit", item_id=UUID(str(item_id)))
    return render(
        request, "admin_shop/item_new.html", _form_context("Новый предмет", form, shop), status=400
    )


def _form_context(title: str, form: Any, shop: ShopSettings, **extra: Any) -> dict[str, Any]:
    return {"title": title, "form": form, "config_error": shop.error, **extra}


def _item_page(
    request: HttpRequest,
    item: dict[str, Any],
    shop: ShopSettings | None = None,
    *,
    form: ItemForm | None = None,
    media_form: ItemMediaForm | None = None,
    status: int = 200,
) -> HttpResponse:
    shop = shop or shop_settings(request)
    limit = shop.media_limit(str(item.get("media_kind")))
    context = _form_context(
        f"Предмет {item.get('key')}",
        form or _item_form(shop, initial=_item_initial(item), editing=True),
        shop,
        item=item,
        media_form=media_form or _media_form(limit_bytes=limit),
        media_limit=limit,
        media=shop.media,
    )
    return render(request, "admin_shop/item_edit.html", context, status=status)


@require_http_methods(["GET", "POST"])
@require_perm(ITEMS)
def item_edit(request: HttpRequest, item_id: UUID) -> HttpResponse:
    """Правка предмета; ключ неизменяем, превью картинки — по ``asset_url``/``poster_url``."""
    item, error = _find_item(request, item_id)
    if error is not None:
        return service_error_response(request, error)
    if item is None:
        raise Http404("Предмет не найден")
    if request.method == "GET":
        return _item_page(request, item)
    form = ItemForm(request.POST, initial=_item_initial(item), editing=True)
    if not form.is_valid():
        return _item_page(request, item, form=form, status=400)
    body, idem = form.payload(), str(form.cleaned_data["idempotency_key"])
    ok, _ = mutate(
        request,
        service=PROGRESSION,
        action="shop.update_item",
        target_type="item",
        target_id=str(item_id),
        reason=body["reason"],
        idempotency_key=idem,
        before={f: item.get(f) for f in _ITEM_FIELDS},
        audit_fields=_ITEM_AUDIT,
        call=_client(lambda c: c.update_item(item_id, body, idempotency_key=idem)),
        success="Предмет сохранён",
    )
    if ok:
        return redirect("shop:item_edit", item_id=item_id)
    return _item_page(request, item, form=form, status=400)


def _media_form(*args: Any, limit_bytes: int, **kwargs: Any) -> ItemMediaForm:
    """Форма загрузки со своими HTML-id: на странице предмета есть и форма параметров."""
    return ItemMediaForm(*args, limit_bytes=limit_bytes, auto_id="id_media_%s", **kwargs)


@require_http_methods(["POST"])
@require_perm(ITEMS)
def item_media(request: HttpRequest, item_id: UUID) -> HttpResponse:
    """Загрузка картинки или animated WebP: размер — по конфигу, до обращения к сервису."""
    item, error = _find_item(request, item_id)
    if error is not None:
        return service_error_response(request, error)
    if item is None:
        raise Http404("Предмет не найден")
    shop = shop_settings(request)  # предел размера берётся из конфига: проверка до отправки
    limit = shop.media_limit(str(item.get("media_kind")))
    form = _media_form(request.POST, request.FILES, limit_bytes=limit)
    if not form.is_valid():
        return _item_page(request, item, shop, media_form=form, status=400)
    upload = form.cleaned_data["file"]
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    ok, _ = mutate(
        request,
        service=PROGRESSION,
        action="shop.upload_item_media",
        target_type="item",
        target_id=str(item_id),
        reason=reason,
        idempotency_key=idem,
        audit_after={"bytes": upload.size, "content_type": upload.content_type},
        call=_client(
            lambda c: c.upload_item_media(
                item_id,
                reason=reason,
                file_name=upload.name or "upload",
                file=upload.file,
                content_type=upload.content_type or "application/octet-stream",
                idempotency_key=idem,
            )
        ),
        success="Картинка загружена",
    )
    if ok:
        return redirect("shop:item_edit", item_id=item_id)
    return _item_page(request, item, shop, media_form=form, status=400)


# ---------------------------------------------------------------------------- паки


def _packs(request: HttpRequest) -> tuple[list[dict[str, Any]], ServiceError | None]:
    data, error = read(request, PROGRESSION, _client(lambda c: c.packs()))
    return as_list(as_dict(data).get("items")), error


@require_GET
@require_perm(ITEMS)
def packs(request: HttpRequest) -> HttpResponse:
    """Список паков (витрины паков нет: продажа выключена, пока ``billing.enabled=false``)."""
    rows, error = _packs(request)
    return render(
        request,
        "admin_shop/packs.html",
        {"title": "Магазин: паки", "rows": rows, "error": error.message if error else None},
    )


def _echo_form(form_class: Any, request: HttpRequest, **kwargs: Any) -> Any:
    """Несвязанная форма с введёнными значениями: без ложных «неверный выбор» при сбое списка.

    Идемпотентный ключ приходит из POST: повторная отправка формы остаётся идемпотентной.
    """
    initial = dict(kwargs.pop("initial", {}))
    initial.update({k: request.POST.get(k) for k in request.POST if k != "csrfmiddlewaretoken"})
    if "item_ids" in request.POST:
        initial["item_ids"] = request.POST.getlist("item_ids")
    return form_class(initial=initial, **kwargs)


def _pack_initial(pack: dict[str, Any]) -> dict[str, Any]:
    fields = ("key", "title", "style", "item_ids", "verses_amount", "price_kopecks", "active")
    return {f: pack.get(f) for f in fields}


def _pack_page(
    request: HttpRequest,
    form: PackForm,
    *,
    pack: dict[str, Any] | None,
    items_error: ServiceError | None = None,
    status: int = 200,
) -> HttpResponse:
    title = f"Пак {pack.get('key')}" if pack else "Новый пак"
    if items_error is not None and request.method == "POST":
        status = error_status(items_error)
    return render(
        request,
        "admin_shop/pack_form.html",
        {
            "title": title,
            "form": form,
            "pack": pack,
            "items_error": f"{ITEMS_LOAD_FAILED}: {items_error.message}" if items_error else None,
        },
        status=status,
    )


def _pack_form(
    request: HttpRequest, *args: Any, pack: dict[str, Any] | None = None, **kwargs: Any
) -> tuple[PackForm, ServiceError | None]:
    rows, error = _scan_items(request)
    form = PackForm(*args, item_choices=_item_choices(rows), editing=pack is not None, **kwargs)
    return form, error


@require_http_methods(["GET", "POST"])
@require_perm(ITEMS)
def pack_new(request: HttpRequest) -> HttpResponse:
    """Создание пака."""
    if request.method == "GET":
        form, items_error = _pack_form(request)
        return _pack_page(request, form, pack=None, items_error=items_error)
    form, items_error = _pack_form(request, request.POST)
    if items_error is not None:  # без списка предметов «неверный выбор» был бы ложным
        return _pack_page(
            request, _echo_form(PackForm, request), pack=None, items_error=items_error
        )
    if not form.is_valid():
        return _pack_page(request, form, pack=None, status=400)
    body, idem = form.payload(), str(form.cleaned_data["idempotency_key"])
    ok, _ = mutate(
        request,
        service=PROGRESSION,
        action="shop.create_pack",
        target_type="pack",
        target_id=body["key"],
        reason=body["reason"],
        idempotency_key=idem,
        audit_fields=("id", "key", "price_kopecks", "verses_amount", "active"),
        call=_client(lambda c: c.create_pack(body, idempotency_key=idem)),
        success="Пак создан",
    )
    if ok:
        return redirect("shop:packs")
    return _pack_page(request, form, pack=None, status=400)


@require_http_methods(["GET", "POST"])
@require_perm(ITEMS)
def pack_edit(request: HttpRequest, pack_id: UUID) -> HttpResponse:
    """Правка пака (ключ неизменяем)."""
    rows, error = _packs(request)
    if error is not None:
        return service_error_response(request, error)
    pack = next((p for p in rows if p.get("id") == str(pack_id)), None)
    if pack is None:
        raise Http404("Пак не найден")
    if request.method == "GET":
        form, items_error = _pack_form(request, pack=pack, initial=_pack_initial(pack))
        return _pack_page(request, form, pack=pack, items_error=items_error)
    form, items_error = _pack_form(request, request.POST, pack=pack, initial=_pack_initial(pack))
    if items_error is not None:
        echo = _echo_form(PackForm, request, editing=True, initial=_pack_initial(pack))
        return _pack_page(request, echo, pack=pack, items_error=items_error)
    if not form.is_valid():
        return _pack_page(request, form, pack=pack, status=400)
    body, idem = form.payload(), str(form.cleaned_data["idempotency_key"])
    ok, _ = mutate(
        request,
        service=PROGRESSION,
        action="shop.update_pack",
        target_type="pack",
        target_id=str(pack_id),
        reason=body["reason"],
        idempotency_key=idem,
        before={k: v for k, v in _pack_initial(pack).items() if k != "item_ids"},
        audit_fields=("id", "key", "price_kopecks", "verses_amount", "active"),
        call=_client(lambda c: c.update_pack(pack_id, body, idempotency_key=idem)),
        success="Пак сохранён",
    )
    if ok:
        return redirect("shop:packs")
    return _pack_page(request, form, pack=pack, status=400)


# ---------------------------------------------------------------------------- промокоды


def _no_store(response: HttpResponse) -> HttpResponse:
    """Страница с кодами не кэшируется ни браузером, ни прокси."""
    response["Cache-Control"] = "no-store, private"
    response["Pragma"] = "no-cache"
    return response


@require_GET
@require_perm(PROMO)
def promos(request: HttpRequest) -> HttpResponse:
    """Партии промокодов (коды не показываются: сервис их не хранит в открытом виде)."""
    cursor = request.GET.get("cursor") or None
    data, error = read(request, PROGRESSION, _client(lambda c: c.promo_batches(cursor=cursor)))
    return render(
        request,
        "admin_shop/promos.html",
        {
            "title": "Промокоды",
            "error": error.message if error else None,
            "rows": as_list(as_dict(data).get("items")),
            "next_cursor": as_dict(data).get("next_cursor"),
        },
    )


def _promo_page(
    request: HttpRequest,
    form: PromoForm,
    status: int = 200,
    items_error: ServiceError | None = None,
) -> HttpResponse:
    return _no_store(
        render(
            request,
            "admin_shop/promo_new.html",
            {
                "title": "Новая партия",
                "form": form,
                "items_error": f"{ITEMS_LOAD_FAILED}: {items_error.message}"
                if items_error
                else None,
            },
            status=status,
        )
    )


@require_http_methods(["GET", "POST"])
@require_perm(PROMO)
def promo_new(request: HttpRequest) -> HttpResponse:
    """Создание партии. Ответ на POST показывает коды один раз и даёт скачать CSV."""
    rows, items_error = _scan_items(request, active=True)
    choices = _item_choices(rows)
    if request.method == "GET":
        return _promo_page(request, PromoForm(item_choices=choices), items_error=items_error)
    form = PromoForm(request.POST, item_choices=choices)
    if items_error is not None and request.POST.get("reward_kind") == "item":
        # Награда предметом без списка предметов: не «неверный выбор», а сбой загрузки списка.
        echo = _echo_form(PromoForm, request, item_choices=choices)
        return _promo_page(request, echo, error_status(items_error), items_error)
    if not form.is_valid():
        return _promo_page(request, form, 400)
    body, idem = form.payload(), str(form.cleaned_data["idempotency_key"])
    ok, result = mutate(
        request,
        service=PROGRESSION,
        action="shop.create_promo",
        target_type="promo_batch",
        target_id=body["label"],
        reason=body["reason"],
        idempotency_key=idem,
        # Только метаданные партии: ``codes`` в аудит не попадают ни при каких условиях.
        audit_after={
            "label": body["label"],
            "count": body["count"],
            "max_uses": body["max_uses"],
            "reward_kind": body["reward"]["kind"],
        },
        call=_client(lambda c: c.create_promo(body, idempotency_key=idem)),
        success="Партия создана",
    )
    if not ok:
        return _promo_page(request, form, 400)
    codes = [str(c) for c in as_str_list(as_dict(result).get("codes"))]
    csv_text = "code\n" + "\n".join(codes) + "\n"
    response = render(
        request,
        "admin_shop/promo_codes.html",
        {
            "title": "Коды партии",
            "label": as_dict(result).get("label"),
            "codes": codes,
            "csv_href": "data:text/csv;charset=utf-8," + quote(csv_text),
        },
    )
    return _no_store(response)


@require_http_methods(["GET", "POST"])
@require_perm(PROMO)
def promo_revoke(request: HttpRequest, promo_id: UUID) -> HttpResponse:
    """Отзыв партии: причина обязательна."""
    if request.method == "GET":
        form = MutationForm()
        return render(
            request, "admin_shop/promo_revoke.html", {"title": "Отозвать партию", "form": form}
        )
    form = MutationForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "admin_shop/promo_revoke.html",
            {"title": "Отозвать партию", "form": form},
            status=400,
        )
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    ok, _ = mutate(
        request,
        service=PROGRESSION,
        action="shop.revoke_promo",
        target_type="promo_batch",
        target_id=str(promo_id),
        reason=reason,
        idempotency_key=idem,
        audit_after={"status": "revoked"},
        call=_client(lambda c: c.revoke_promo(promo_id, reason=reason, idempotency_key=idem)),
        success="Партия отозвана",
    )
    if ok:
        return redirect("shop:promos")
    return render(
        request,
        "admin_shop/promo_revoke.html",
        {"title": "Отозвать партию", "form": form},
        status=400,
    )
