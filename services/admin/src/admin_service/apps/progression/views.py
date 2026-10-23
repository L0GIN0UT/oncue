"""Представления раздела «Прогрессия»: конфиг, колесо, задания и бейджи, журнал Версов.

Конфиги и журналы хранит progression (ADR 0042, 0043): чтение и запись - только через его
admin-API (``core.progression_client``), прямой записи в БД progression нет. Права проверяются
здесь (кнопки без права не показываются, действие без права - 403) и ещё раз в progression:
``progression.config.manage`` - черновик и чтение, ``config.publish`` - публикация и откат,
``ledger.grant|reverse|read`` - выдача, сторно и журнал. Каждая мутация пишет ``AuditEntry`` и
при успехе, и при отказе (``core.views.mutate``). Тело конфига в аудит не пишется.
Выдача и сторно - ledger (повышенная безопасность): причина обязательна, лимиты проверяет
сервис, ключ идемпотентности приходит скрытым полем формы.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from admin_service.apps.game_config import config_text
from admin_service.apps.game_config.forms import EditorForm
from admin_service.apps.progression.forms import (
    GrantForm,
    LedgerSearchForm,
    PublishForm,
    ReverseForm,
    RollbackForm,
    parse_user_id,
)
from admin_service.apps.progression.wheel import sector_chances
from admin_service.core.permissions import has_perm, require_perm
from admin_service.core.progression_client import PROGRESSION, ProgressionAdminClient
from admin_service.core.service_client import ServiceClient, ServiceError
from admin_service.core.views import mutate, read

MANAGE = "progression.config.manage"
PUBLISH = "progression.config.publish"
GRANT = "progression.ledger.grant"
REVERSE = "progression.ledger.reverse"
LEDGER_READ = "progression.ledger.read"
_INVALID_STATUSES = (400, 422)
_DRAFT, _PUBLISHED = "draft", "published"
_VERSION_FIELDS = ("card",)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _int_param(raw: str | None) -> int | None:
    try:
        value = int(raw) if raw else None
    except ValueError:
        return None
    return value if value is not None and value >= 1 else None


def _client(call: Callable[[ProgressionAdminClient], Any]) -> Callable[[ServiceClient], Any]:
    return lambda c: call(ProgressionAdminClient(c))


def _version_config(request: HttpRequest, version: int | None) -> tuple[Any, str | None]:
    if version is None:
        return None, None
    data, error = read(request, PROGRESSION, _client(lambda c: c.version(version)))
    if error is not None:
        return None, error.message
    return _dict(data).get("config"), None


def _statuses(cards: list[dict[str, Any]]) -> tuple[int | None, int | None]:
    """Номера черновика и действующей версии (карточки - новые первыми)."""
    found: dict[str, int | None] = {_DRAFT: None, _PUBLISHED: None}
    for card in cards:
        status = str(card.get("status"))
        if status in found and found[status] is None:
            found[status] = _int(card.get("version"))
    return found[_DRAFT], found[_PUBLISHED]


# ---------------------------------------------------------------------------- версии


def _publish_initial(cards: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Номер и хеш тела черновика, как их показал список: публикация привязана к хешу."""
    draft = next((c for c in cards if c.get("status") == _DRAFT), None)
    if draft is None:
        return None
    return {"version": draft.get("version"), "body_sha256": draft.get("body_sha256")}


def _configs_context(request: HttpRequest, **forms: Any) -> dict[str, Any]:
    data, error = read(request, PROGRESSION, _client(lambda c: c.versions()))
    context: dict[str, Any] = {
        "title": "Прогрессия: конфиг",
        "error": error.message if error else None,
        "can_publish": has_perm(request, PUBLISH),
    }
    if error is not None:
        return context
    versions = _list(_dict(data).get("items"))
    draft, published = _statuses(versions)
    old_v = _int_param(request.GET.get("from")) or published
    new_v = _int_param(request.GET.get("to")) or draft
    diff: list[config_text.DiffLine] = []
    diff_error = None
    if old_v is not None and new_v is not None and old_v != new_v:
        old_cfg, e1 = _version_config(request, old_v)
        new_cfg, e2 = _version_config(request, new_v)
        diff_error = e1 or e2
        if diff_error is None:
            diff = config_text.diff(old_cfg, new_cfg, old_label=f"v{old_v}", new_label=f"v{new_v}")
    shown = _int_param(request.GET.get("version"))
    shown_cfg, shown_error = _version_config(request, shown)
    rollback_versions = [
        v for v in (_int(x.get("version")) for x in versions if x.get("status") != _DRAFT) if v
    ]
    context.update(
        versions=versions,
        draft=draft,
        published=published,
        diff=diff,
        diff_error=diff_error,
        diff_from=old_v,
        diff_to=new_v,
        shown=shown,
        shown_text=config_text.dump(shown_cfg, "yaml") if shown_cfg is not None else None,
        shown_error=shown_error,
        publish_form=forms.get("publish_form") or PublishForm(initial=_publish_initial(versions)),
        rollback_form=forms.get("rollback_form") or RollbackForm(versions=rollback_versions),
    )
    return context


@require_GET
@require_perm(MANAGE)
def configs(request: HttpRequest) -> HttpResponse:
    """Версии конфига: список, тело версии, diff черновика с действующей, публикация, откат."""
    return render(request, "admin_progression/configs.html", _configs_context(request))


@require_POST
@require_perm(PUBLISH)
def config_publish(request: HttpRequest) -> HttpResponse:
    """Опубликовать черновик (действует для новых проводок, прошлое не пересчитывается)."""
    form = PublishForm(request.POST)
    if not form.is_valid():
        context = _configs_context(request, publish_form=form)
        return render(request, "admin_progression/configs.html", context, status=400)
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    version, body_hash = form.cleaned_data["version"], form.cleaned_data["body_sha256"]
    mutate(
        request,
        service=PROGRESSION,
        action="progression.publish_config",
        target_type="config",
        target_id=str(version),
        reason=reason,
        idempotency_key=idem,
        before={"draft_version": version},
        audit_fields=_VERSION_FIELDS,
        call=_client(
            lambda c: c.publish(
                version=version, body_sha256=body_hash, reason=reason, idempotency_key=idem
            )
        ),
        success=f"Версия v{version} опубликована",
    )
    return redirect("progression:configs")


@require_POST
@require_perm(PUBLISH)
def config_rollback(request: HttpRequest) -> HttpResponse:
    """Откат: копия выбранной версии становится черновиком (заменяет текущий)."""
    form = RollbackForm(request.POST)
    if not form.is_valid():
        context = _configs_context(request, rollback_form=form)
        return render(request, "admin_progression/configs.html", context, status=400)
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    to_version = form.cleaned_data["to_version"]
    mutate(
        request,
        service=PROGRESSION,
        action="progression.rollback_config",
        target_type="config",
        target_id=str(to_version),
        reason=reason,
        idempotency_key=idem,
        audit_fields=_VERSION_FIELDS,
        call=_client(lambda c: c.rollback(to_version, reason=reason, idempotency_key=idem)),
        success=f"Версия v{to_version} скопирована в черновик",
    )
    return redirect("progression:configs")


# ---------------------------------------------------------------------------- редактор


def error_rows(text: str, *, details: Any, errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ошибки ``progression.config_invalid`` (``{path, reason}``) и валидации запроса."""
    rows: list[dict[str, Any]] = []
    items = _dict(details).get("errors")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        rows.append(
            {
                "path": path or "(корень)",
                "message": str(item.get("reason") or item.get("message") or ""),
                "line": config_text.line_of(text, path) if path else None,
            }
        )
    for item in errors:
        loc = item.get("loc")
        path = (
            ".".join(str(p) for p in loc if p not in ("body", "config"))
            if isinstance(loc, list)
            else ""
        )
        rows.append(
            {
                "path": path or "(корень)",
                "message": str(item.get("msg") or ""),
                "line": config_text.line_of(text, path) if path else None,
            }
        )
    return rows


def _capturing(
    call: Callable[[ProgressionAdminClient], Any], sink: list[ServiceError]
) -> Callable[[ServiceClient], Any]:
    def run(client: ServiceClient) -> Any:
        try:
            return call(ProgressionAdminClient(client))
        except ServiceError as exc:
            sink.append(exc)
            raise

    return run


def _initial_editor(request: HttpRequest, fmt: config_text.Format) -> dict[str, Any]:
    """Начальный текст: черновик, иначе действующая версия."""
    data, error = read(request, PROGRESSION, _client(lambda c: c.versions()))
    if error is not None:
        return {"error": error.message, "form": None}
    cards = _list(_dict(data).get("items"))
    draft, published = _statuses(cards)
    version = draft or published
    config, config_error = _version_config(request, version)
    if config_error is not None:
        return {"error": config_error, "form": None}
    text = config_text.dump(config if isinstance(config, dict) else {}, fmt)
    return {
        "error": None,
        "form": EditorForm(initial={"fmt": fmt, "text": text}),
        "based_on": version,
        "based_status": _DRAFT if draft else _PUBLISHED,
    }


def _render_editor(request: HttpRequest, *, status: int = 200, **context: Any) -> HttpResponse:
    return render(
        request,
        "admin_progression/edit.html",
        {"title": "Прогрессия: черновик конфига", **context},
        status=status,
    )


@require_http_methods(["GET", "POST"])
@require_perm(MANAGE)
def config_edit(request: HttpRequest) -> HttpResponse:
    """Редактор конфига (YAML/JSON): сохранение черновика.

    Полную проверку делает progression: 422 ``progression.config_invalid`` - таблица ошибок
    с номерами строк, текст остаётся в форме.
    """
    if request.method == "GET":
        fmt: config_text.Format = "json" if request.GET.get("fmt") == "json" else "yaml"
        return _render_editor(request, **_initial_editor(request, fmt))

    form = EditorForm(request.POST)
    if not form.is_valid() or form.body is None or not form.require_reason():
        return _render_editor(request, form=form, status=400)
    body = form.body
    text: str = form.cleaned_data["text"]
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    errors: list[ServiceError] = []
    ok, _ = mutate(
        request,
        service=PROGRESSION,
        action="progression.save_draft",
        target_type="config",
        target_id="draft",
        reason=reason,
        idempotency_key=idem,
        audit_fields=_VERSION_FIELDS,
        call=_capturing(
            lambda c: c.save_draft(config=body, reason=reason, idempotency_key=idem), errors
        ),
        success="Черновик сохранён",
    )
    if ok:
        return redirect("progression:configs")
    context: dict[str, Any] = {}
    if errors and errors[0].status in _INVALID_STATUSES:
        rows = error_rows(text, details=errors[0].details, errors=errors[0].errors)
        marked = {r["line"] for r in rows if r["line"] is not None}
        context = {"field_errors": rows, "lines": config_text.numbered(text, marked)}
    return _render_editor(request, form=form, **context)


# ---------------------------------------------------------------------------- колесо


@require_GET
@require_perm(MANAGE)
def wheel_preview(request: HttpRequest) -> HttpResponse:
    """Предпросмотр вероятностей колеса: веса секторов версии -> шансы (считает админка)."""
    data, error = read(request, PROGRESSION, _client(lambda c: c.versions()))
    versions = _list(_dict(data).get("items"))
    draft, published = _statuses(versions)
    version = _int_param(request.GET.get("version")) or draft or published
    config, config_error = _version_config(request, version) if error is None else (None, None)
    sectors = sector_chances(config)
    return render(
        request,
        "admin_progression/wheel.html",
        {
            "title": "Прогрессия: колесо",
            "error": (error.message if error else None) or config_error,
            "versions": versions,
            "version": version,
            "sectors": sectors,
            "total_weight": sum(s.weight for s in sectors),
            "enabled": bool(_dict(_dict(config).get("wheel")).get("enabled")),
        },
    )


# ---------------------------------------------------------------------------- задания, бейджи


@require_GET
@require_perm(MANAGE)
def quests_badges(request: HttpRequest) -> HttpResponse:
    """Задания и бейджи действующего конфига (чтение; правка - через черновик)."""
    quests, quests_error = read(request, PROGRESSION, _client(lambda c: c.quests()))
    badges, badges_error = read(request, PROGRESSION, _client(lambda c: c.badges()))
    failure = quests_error or badges_error
    return render(
        request,
        "admin_progression/quests.html",
        {
            "title": "Прогрессия: задания и бейджи",
            "error": failure.message if failure else None,
            "config_version": _dict(quests).get("config_version"),
            "quests": _list(_dict(quests).get("items")),
            "badges": _list(_dict(badges).get("items")),
        },
    )


# ---------------------------------------------------------------------------- журнал Версов


@require_http_methods(["GET"])
@require_perm(LEDGER_READ)
def ledger_search(request: HttpRequest) -> HttpResponse:
    """Поиск журнала по ID игрока."""
    form = LedgerSearchForm(request.GET or None)
    if form.is_bound and form.is_valid():
        return redirect("progression:ledger_user", user_id=form.cleaned_data["user_id"])
    return render(
        request,
        "admin_progression/ledger_search.html",
        {"title": "Версы: журнал игрока", "form": form},
        status=400 if form.is_bound else 200,
    )


def _ledger_context(request: HttpRequest, user_id: UUID, **forms: Any) -> dict[str, Any]:
    cursor = request.GET.get("cursor") or None
    data, error = read(
        request, PROGRESSION, _client(lambda c: c.ledger(user_id, cursor=cursor, limit=50))
    )
    can_reverse = has_perm(request, REVERSE)
    items = _list(_dict(data).get("items"))
    rows = [
        {
            **item,
            # Сторнируются только кредиты, не сторно; у формы свои HTML-id (`auto_id`).
            "reverse_form": ReverseForm(
                initial={"op_id": item.get("op_id")}, auto_id=f"id_rev{n}_%s"
            )
            if can_reverse and not item.get("reversal_of") and item.get("direction") == "credit"
            else None,
        }
        for n, item in enumerate(items)
    ]
    return {
        "title": "Версы: журнал игрока",
        "user_id": user_id,
        "error": error.message if error else None,
        "not_found": error is not None and error.status == 404,  # noqa: PLR2004
        "earned": _dict(data).get("earned"),
        "paid": _dict(data).get("paid"),
        "rows": rows,
        "next_cursor": _dict(data).get("next_cursor"),
        "can_grant": has_perm(request, GRANT),
        "can_reverse": can_reverse,
        "grant_form": forms.get("grant_form") or GrantForm(auto_id="id_grant_%s"),
        "reverse_form_bound": forms.get("reverse_form"),
    }


@require_GET
@require_perm(LEDGER_READ)
def ledger_user(request: HttpRequest, user_id: UUID) -> HttpResponse:
    """Журнал игрока (курсор), балансы, выдача и сторно по правам."""
    context = _ledger_context(request, user_id)
    status = 404 if context["not_found"] else 200
    return render(request, "admin_progression/ledger_user.html", context, status=status)


@require_POST
@require_perm(GRANT)
def ledger_grant(request: HttpRequest, user_id: UUID) -> HttpResponse:
    """Ручная выдача Версов (причина обязательна; лимиты и аудит - в сервисе и здесь)."""
    form = GrantForm(request.POST, auto_id="id_grant_%s")
    if not form.is_valid():
        context = _ledger_context(request, user_id, grant_form=form)
        return render(request, "admin_progression/ledger_user.html", context, status=400)
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    amount = form.cleaned_data["amount"]
    mutate(
        request,
        service=PROGRESSION,
        action="progression.grant",
        target_type="user",
        target_id=str(user_id),
        reason=reason,
        idempotency_key=idem,
        before={"amount": amount},
        audit_fields=("balance_after",),
        call=_client(
            lambda c: c.grant(user_id, amount=amount, reason=reason, idempotency_key=idem)
        ),
        success=f"Выдано {amount} Версов",
    )
    return redirect(reverse("progression:ledger_user", kwargs={"user_id": user_id}))


@require_POST
@require_perm(REVERSE)
def ledger_reverse(request: HttpRequest, user_id: UUID) -> HttpResponse:
    """Сторно проводки (встречная проводка; баланс не может уйти ниже нуля)."""
    form = ReverseForm(request.POST, auto_id="id_rev_err_%s")
    if not form.is_valid():
        context = _ledger_context(request, user_id, reverse_form=form)
        return render(request, "admin_progression/ledger_user.html", context, status=400)
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    op_id = form.cleaned_data["op_id"]
    ok, result = mutate(
        request,
        service=PROGRESSION,
        action="progression.reverse",
        target_type="ledger",
        target_id=op_id,
        reason=reason,
        idempotency_key=idem,
        # Игрок - из ответа сервиса (владелец проводки), а не из URL: URL только для возврата.
        audit_fields=("user_id", "balance_after"),
        call=_client(lambda c: c.reverse(op_id, reason=reason, idempotency_key=idem)),
        success="Проводка сторнирована",
    )
    owner = parse_user_id(_dict(result).get("user_id")) if ok else None
    return redirect(reverse("progression:ledger_user", kwargs={"user_id": owner or user_id}))
