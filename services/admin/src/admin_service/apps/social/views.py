"""Представления раздела «Модерация»: очередь, игрок и санкции, конфиг social.

Данные и действия - только через admin-API social (``core.social_client``); прав на запись в БД
social у админки нет. Права проверяются здесь (кнопки без права скрыты, действие без права - 403)
и ещё раз в social: ``social.moderate`` - очередь и игрок, ``social.sanctions.manage`` -
санкции (``account`` - ещё ``social.sanctions.account``), ``social.config.manage|publish`` -
конфиг. Каждая мутация пишет ``AuditEntry`` и при успехе, и при отказе (``core.views.mutate``).
Тексты жалоб и комментариев выводятся только экранированными (автоэкранирование шаблонов,
``|safe`` не используется) и в аудит не попадают; просмотр кейса - ``audit_read``.
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
from admin_service.apps.progression.forms import PublishForm, RollbackForm
from admin_service.apps.progression.views import error_rows
from admin_service.apps.social.forms import (
    CaseFilterForm,
    ClaimForm,
    EscalateForm,
    GrantSanctionForm,
    PlayerSearchForm,
    ResolveForm,
    RevokeSanctionForm,
    parse_uuid,
)
from admin_service.core.payload import as_dict, as_list
from admin_service.core.permissions import has_perm, require_perm
from admin_service.core.service_client import ServiceClient, ServiceError
from admin_service.core.social_client import SOCIAL, SocialAdminClient
from admin_service.core.views import audit_read, mutate, read, service_error_response
from gv_core.ids import uuid7

MODERATE = "social.moderate"
SANCTIONS = "social.sanctions.manage"
SENIOR = "social.sanctions.account"
CONFIG_MANAGE = "social.config.manage"
CONFIG_PUBLISH = "social.config.publish"
_INVALID_STATUSES = (400, 422)
_DRAFT, _PUBLISHED = "draft", "published"
_CASE_FIELDS = ("status", "version", "decision")
_VERSION_FIELDS = ("card",)


def _client(call: Callable[[SocialAdminClient], Any]) -> Callable[[ServiceClient], Any]:
    return lambda c: call(SocialAdminClient(c))


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _int_param(raw: str | None) -> int | None:
    try:
        value = int(raw) if raw else None
    except ValueError:
        return None
    return value if value is not None and value >= 1 else None


# ---------------------------------------------------------------------------- очередь


@require_GET
@require_perm(MODERATE)
def cases(request: HttpRequest) -> HttpResponse:
    """Очередь модерации: старые первыми, фильтры по статусу, объекту и «мои»."""
    form = CaseFilterForm(request.GET or None)
    valid = form.is_valid() if request.GET else True
    filters = form.cleaned_data if request.GET and valid else {}
    cursor = request.GET.get("cursor") or None
    data, error = read(
        request,
        SOCIAL,
        _client(
            lambda c: c.cases(
                status=filters.get("status") or None,
                target_kind=filters.get("target_kind") or None,
                mine=bool(filters.get("mine")),
                cursor=cursor,
            )
        ),
    )
    items = []
    for row in as_list(as_dict(data).get("items")):
        case_id = parse_uuid(str(row.get("id")))
        items.append(
            {
                **row,
                "admin_url": reverse("social:case", kwargs={"case_id": case_id})
                if case_id
                else None,
            }
        )
    query = request.GET.copy()
    query.pop("cursor", None)
    return render(
        request,
        "admin_social/cases.html",
        {
            "title": "Очередь модерации",
            "filter_form": form,
            "error": error.message if error else None,
            "rows": items,
            "next_cursor": as_dict(data).get("next_cursor"),
            "query": query.urlencode(),
        },
    )


def _case_context(
    request: HttpRequest, case_id: UUID, **forms_: Any
) -> tuple[dict[str, Any] | None, ServiceError | None]:
    data, error = read(request, SOCIAL, _client(lambda c: c.case(case_id)))
    if error is not None:
        return None, error
    case = as_dict(data)
    version = _int(case.get("version")) or 1
    status = str(case.get("status"))
    me = str(getattr(request.user, "identity_user_id", "") or "")
    open_ = status in ("open", "in_work", "escalated")
    escalated = status == "escalated"
    can_resolve = (status == "in_work") or (escalated and has_perm(request, SENIOR))
    initial = {"version": version}
    context: dict[str, Any] = {
        "title": "Кейс модерации",
        "case": case,
        "reports": as_list(case.get("reports")),
        "can_claim": status == "open",
        "can_escalate": status == "in_work" and bool(me) and str(case.get("assignee_id")) == me,
        "can_resolve": open_ and can_resolve,
        "escalated_waiting": escalated and not has_perm(request, SENIOR),
        "can_sanction": has_perm(request, SANCTIONS),
        "claim_form": forms_.get("claim_form") or ClaimForm(initial=initial),
        "escalate_form": forms_.get("escalate_form") or EscalateForm(initial=initial),
        "resolve_form": forms_.get("resolve_form") or ResolveForm(initial=initial),
        "player_url": reverse("social:player", kwargs={"user_id": case.get("target_user_id")})
        if parse_uuid(str(case.get("target_user_id")))
        else None,
    }
    return context, None


@require_GET
@require_perm(MODERATE)
def case_detail(request: HttpRequest, case_id: UUID) -> HttpResponse:
    """Карточка кейса: жалобы (текст экранирован), фрагмент содержимого, действия."""
    context, error = _case_context(request, case_id)
    if error is not None or context is None:
        return service_error_response(request, error) if error else HttpResponse(status=500)
    audit_read(
        request,
        action="social.view_case",
        service=SOCIAL,
        target_type="case",
        target_id=str(case_id),
    )
    response = render(request, "admin_social/case.html", context)
    response["Cache-Control"] = "no-store"
    return response


def _back(case_id: UUID) -> HttpResponse:
    return redirect("social:case", case_id=case_id)


@require_POST
@require_perm(MODERATE)
def case_claim(request: HttpRequest, case_id: UUID) -> HttpResponse:
    """Взять кейс в работу (409: уже взят или версия устарела - сообщение)."""
    form = ClaimForm(request.POST)
    if not form.is_valid():
        return _invalid(request, case_id, claim_form=form)
    version, idem = form.cleaned_data["version"], str(form.cleaned_data["idempotency_key"])
    mutate(
        request,
        service=SOCIAL,
        action="social.case_claim",
        target_type="case",
        target_id=str(case_id),
        reason=form.cleaned_data["reason"],
        idempotency_key=idem,
        before={"version": version},
        audit_fields=_CASE_FIELDS,
        call=_client(lambda c: c.claim(case_id, version=version, idempotency_key=idem)),
        success="Кейс взят в работу",
    )
    return _back(case_id)


@require_POST
@require_perm(MODERATE)
def case_escalate(request: HttpRequest, case_id: UUID) -> HttpResponse:
    """Эскалировать кейс (дальше его решает только старший)."""
    form = EscalateForm(request.POST)
    if not form.is_valid():
        return _invalid(request, case_id, escalate_form=form)
    version, reason = form.cleaned_data["version"], form.cleaned_data["reason"]
    idem = str(form.cleaned_data["idempotency_key"])
    mutate(
        request,
        service=SOCIAL,
        action="social.case_escalate",
        target_type="case",
        target_id=str(case_id),
        reason=reason,
        idempotency_key=idem,
        before={"version": version},
        audit_fields=_CASE_FIELDS,
        call=_client(
            lambda c: c.escalate(case_id, version=version, reason=reason, idempotency_key=idem)
        ),
        success="Кейс эскалирован",
    )
    return _back(case_id)


@require_POST
@require_perm(MODERATE)
def case_resolve(request: HttpRequest, case_id: UUID) -> HttpResponse:
    """Решить кейс. Санкция в решении требует ещё ``social.sanctions.manage``."""
    form = ResolveForm(request.POST)
    if form.is_valid() and form.cleaned_data["decision"] == "sanctioned":
        if not has_perm(request, SANCTIONS):
            form.add_error(None, "Нет права выдавать санкции")
        elif form.cleaned_data["sanction_kind"] == "account" and not has_perm(request, SENIOR):
            form.add_error(None, "Санкцию на аккаунт выдаёт только старший модератор")
    if not form.is_valid():
        return _invalid(request, case_id, resolve_form=form)
    data = form.cleaned_data
    version, reason, idem = data["version"], data["reason"], str(data["idempotency_key"])
    sanction = form.sanction()
    mutate(
        request,
        service=SOCIAL,
        action="social.case_resolve",
        target_type="case",
        target_id=str(case_id),
        reason=reason,
        idempotency_key=idem,
        before={"version": version},
        audit_fields=_CASE_FIELDS,
        audit_after=None,
        call=_client(
            lambda c: c.resolve(
                case_id,
                version=version,
                decision=data["decision"],
                reason=reason,
                idempotency_key=idem,
                sanction=sanction,
            )
        ),
        success="Кейс решён",
    )
    return _back(case_id)


def _invalid(request: HttpRequest, case_id: UUID, **forms_: Any) -> HttpResponse:
    context, error = _case_context(request, case_id, **forms_)
    if error is not None or context is None:
        return service_error_response(request, error) if error else HttpResponse(status=500)
    return render(request, "admin_social/case.html", context, status=400)


# ---------------------------------------------------------------------------- игрок


@require_http_methods(["GET"])
@require_perm(MODERATE)
def player_search(request: HttpRequest) -> HttpResponse:
    """Поиск игрока по идентификатору: переход на его карточку."""
    form = PlayerSearchForm(request.GET or None)
    if request.GET and form.is_valid():
        return redirect("social:player", user_id=form.cleaned_data["user_id"])
    return render(request, "admin_social/player_search.html", {"title": "Игрок", "form": form})


def _player_context(request: HttpRequest, user_id: UUID, **forms_: Any) -> dict[str, Any]:
    data, error = read(request, SOCIAL, _client(lambda c: c.user_moderation(user_id)))
    player = as_dict(data)
    # Ключ идемпотентности на каждую форму снятия: повтор той же формы - тот же ключ.
    sanctions = [{**s, "idem": str(uuid7())} for s in as_list(player.get("sanctions"))]
    return {
        "title": "Игрок: модерация",
        "user_id": user_id,
        "error": error.message if error else None,
        "player": player,
        "counters": as_dict(player.get("counters")),
        "sanctions": sanctions,
        "player_cases": [
            {**c, "admin_url": reverse("social:case", kwargs={"case_id": c["id"]})}
            for c in as_list(player.get("cases"))
            if parse_uuid(str(c.get("id")))
        ],
        "can_sanction": has_perm(request, SANCTIONS),
        "grant_form": forms_.get("grant_form") or GrantSanctionForm(),
        "revoke_form": forms_.get("revoke_form") or RevokeSanctionForm(),
    }


@require_GET
@require_perm(MODERATE)
def player(request: HttpRequest, user_id: UUID) -> HttpResponse:
    """Карточка игрока: счётчики порядочности (автосанкций нет), санкции, кейсы."""
    context = _player_context(request, user_id)
    audit_read(
        request,
        action="social.view_player",
        service=SOCIAL,
        target_type="user",
        target_id=str(user_id),
    )
    response = render(request, "admin_social/player.html", context)
    response["Cache-Control"] = "no-store"
    return response


@require_POST
@require_perm(SANCTIONS)
def sanction_grant(request: HttpRequest, user_id: UUID) -> HttpResponse:
    """Выдать санкцию (самому себе, ``account`` без права и пределы срока проверяет social)."""
    form = GrantSanctionForm(request.POST)
    if form.is_valid() and form.cleaned_data["kind"] == "account" and not has_perm(request, SENIOR):
        form.add_error("kind", "Санкцию на аккаунт выдаёт только старший модератор")
    if not form.is_valid():
        context = _player_context(request, user_id, grant_form=form)
        return render(request, "admin_social/player.html", context, status=400)
    data = form.cleaned_data
    idem = str(data["idempotency_key"])
    mutate(
        request,
        service=SOCIAL,
        action="social.sanction_grant",
        target_type="user",
        target_id=str(user_id),
        reason=data["reason"],
        idempotency_key=idem,
        before={"kind": data["kind"], "duration_s": data.get("duration_s")},
        audit_fields=("id", "kind", "expires_at"),
        call=_client(
            lambda c: c.grant_sanction(
                user_id,
                kind=data["kind"],
                duration_s=data.get("duration_s"),
                reason=data["reason"],
                idempotency_key=idem,
            )
        ),
        success="Санкция выдана",
    )
    return redirect("social:player", user_id=user_id)


@require_POST
@require_perm(SANCTIONS)
def sanction_revoke(request: HttpRequest, user_id: UUID) -> HttpResponse:
    """Снять санкцию вручную (история сохраняется)."""
    form = RevokeSanctionForm(request.POST)
    if not form.is_valid():
        context = _player_context(request, user_id, revoke_form=form)
        return render(request, "admin_social/player.html", context, status=400)
    data = form.cleaned_data
    sanction_id, idem = data["sanction_id"], str(data["idempotency_key"])
    mutate(
        request,
        service=SOCIAL,
        action="social.sanction_revoke",
        target_type="sanction",
        target_id=str(sanction_id),
        reason=data["reason"],
        idempotency_key=idem,
        before={"user_id": str(user_id)},
        audit_fields=("id", "kind", "revoked_at"),
        call=_client(
            lambda c: c.revoke_sanction(sanction_id, reason=data["reason"], idempotency_key=idem)
        ),
        success="Санкция снята",
    )
    return redirect("social:player", user_id=user_id)


# ---------------------------------------------------------------------------- конфиг


def _statuses(cards: list[dict[str, Any]]) -> tuple[int | None, int | None]:
    """Номера черновика и действующей версии (карточки - новые первыми)."""
    found: dict[str, int | None] = {_DRAFT: None, _PUBLISHED: None}
    for card in cards:
        status = str(card.get("status"))
        if status in found and found[status] is None:
            found[status] = _int(card.get("version"))
    return found[_DRAFT], found[_PUBLISHED]


def _version_config(request: HttpRequest, version: int | None) -> tuple[Any, str | None]:
    if version is None:
        return None, None
    data, error = read(request, SOCIAL, _client(lambda c: c.version(version)))
    if error is not None:
        return None, error.message
    return as_dict(data).get("config"), None


def _sections(config: Any) -> list[str]:
    """Верхние разделы конфига - подсказка редактору (границы значений проверяет social)."""
    return sorted(str(k) for k in config) if isinstance(config, dict) else []


def _configs_context(request: HttpRequest, **forms_: Any) -> dict[str, Any]:
    data, error = read(request, SOCIAL, _client(lambda c: c.versions()))
    context: dict[str, Any] = {
        "title": "Конфиг social",
        "error": error.message if error else None,
        "can_publish": has_perm(request, CONFIG_PUBLISH),
    }
    if error is not None:
        return context
    versions = as_list(as_dict(data).get("items"))
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
    draft_card = next((c for c in versions if c.get("status") == _DRAFT), None)
    initial = (
        {"version": draft_card.get("version"), "body_sha256": draft_card.get("body_sha256")}
        if draft_card
        else None
    )
    rollback_versions = [
        v for v in (_int(x.get("version")) for x in versions if x.get("status") != _DRAFT) if v
    ]
    context.update(
        versions=versions,
        draft=draft,
        published=published,
        draft_sha=(draft_card or {}).get("body_sha256"),
        diff=diff,
        diff_error=diff_error,
        diff_from=old_v,
        diff_to=new_v,
        shown=shown,
        shown_text=config_text.dump(shown_cfg, "yaml") if shown_cfg is not None else None,
        shown_error=shown_error,
        publish_form=forms_.get("publish_form") or PublishForm(initial=initial),
        rollback_form=forms_.get("rollback_form") or RollbackForm(versions=rollback_versions),
    )
    return context


@require_GET
@require_perm(CONFIG_MANAGE)
def configs(request: HttpRequest) -> HttpResponse:
    """Версии конфига: список, тело версии, diff, публикация (с хешем) и откат."""
    return render(request, "admin_social/configs.html", _configs_context(request))


@require_POST
@require_perm(CONFIG_PUBLISH)
def config_publish(request: HttpRequest) -> HttpResponse:
    """Опубликовать черновик: привязка к номеру и хешу тела, которые админ видел."""
    form = PublishForm(request.POST)
    if not form.is_valid():
        context = _configs_context(request, publish_form=form)
        return render(request, "admin_social/configs.html", context, status=400)
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    version, body_hash = form.cleaned_data["version"], form.cleaned_data["body_sha256"]
    mutate(
        request,
        service=SOCIAL,
        action="social.publish_config",
        target_type="config",
        target_id=str(version),
        reason=reason,
        idempotency_key=idem,
        before={"draft_version": version, "body_sha256": body_hash},
        audit_fields=_VERSION_FIELDS,
        call=_client(
            lambda c: c.publish(
                version=version, body_sha256=body_hash, reason=reason, idempotency_key=idem
            )
        ),
        success=f"Версия v{version} опубликована",
    )
    return redirect("social:configs")


@require_POST
@require_perm(CONFIG_PUBLISH)
def config_rollback(request: HttpRequest) -> HttpResponse:
    """Откат: копия выбранной версии становится черновиком (заменяет текущий)."""
    form = RollbackForm(request.POST)
    if not form.is_valid():
        context = _configs_context(request, rollback_form=form)
        return render(request, "admin_social/configs.html", context, status=400)
    reason, idem = form.cleaned_data["reason"], str(form.cleaned_data["idempotency_key"])
    to_version = form.cleaned_data["to_version"]
    mutate(
        request,
        service=SOCIAL,
        action="social.rollback_config",
        target_type="config",
        target_id=str(to_version),
        reason=reason,
        idempotency_key=idem,
        audit_fields=_VERSION_FIELDS,
        call=_client(lambda c: c.rollback(to_version, reason=reason, idempotency_key=idem)),
        success=f"Версия v{to_version} скопирована в черновик",
    )
    return redirect("social:configs")


def _capturing(
    call: Callable[[SocialAdminClient], Any], sink: list[ServiceError]
) -> Callable[[ServiceClient], Any]:
    def run(client: ServiceClient) -> Any:
        try:
            return call(SocialAdminClient(client))
        except ServiceError as exc:
            sink.append(exc)
            raise

    return run


def _initial_editor(request: HttpRequest, fmt: config_text.Format) -> dict[str, Any]:
    """Начальный текст: черновик, иначе действующая версия."""
    data, error = read(request, SOCIAL, _client(lambda c: c.versions()))
    if error is not None:
        return {"error": error.message, "form": None}
    draft, published = _statuses(as_list(as_dict(data).get("items")))
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
        "sections": _sections(config),
    }


def _render_editor(request: HttpRequest, *, status: int = 200, **context: Any) -> HttpResponse:
    return render(
        request,
        "admin_social/edit.html",
        {"title": "Конфиг social: черновик", **context},
        status=status,
    )


@require_http_methods(["GET", "POST"])
@require_perm(CONFIG_MANAGE)
def config_edit(request: HttpRequest) -> HttpResponse:
    """Редактор конфига (YAML/JSON): сохранение черновика.

    Полную проверку по схеме контракта и границам делает social: 422 ``social.config_invalid``
    (``details.errors[{path, reason}]``) - таблица ошибок с номерами строк, текст остаётся в форме.
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
        service=SOCIAL,
        action="social.save_draft",
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
        return redirect("social:configs")
    context: dict[str, Any] = {}
    if errors and errors[0].status in _INVALID_STATUSES:
        rows = error_rows(text, details=errors[0].details, errors=errors[0].errors)
        marked = {r["line"] for r in rows if r["line"] is not None}
        context = {"field_errors": rows, "lines": config_text.numbered(text, marked)}
    return _render_editor(request, form=form, sections=_sections(body), **context)
