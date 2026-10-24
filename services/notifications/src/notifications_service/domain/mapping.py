"""Маппинг доменного события в уведомление (ADR 0047).

Все правила (получатель, фильтр, подстановки, ключ дедупликации) приходят из типа конфига;
здесь только механика. Ключ дедупликации собирается из полей ``payload`` («предмет события»),
``event_id`` в нём не участвует: повтор события с новым ``event_id`` не создаёт второй записи.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from notifications_service.domain.catalog import (
    DEDUP_PLACEHOLDER,
    PAYLOAD_ROOT,
    PLACEHOLDER,
    FilterValue,
    NotificationType,
)
from notifications_service.domain.errors import MappingError
from notifications_service.domain.notification import Candidate

DEDUP_KEY_MAX = 200
PARAM_MAX = 120
TITLE_MAX = 120
BODY_MAX = 500


def resolve_path(payload: Mapping[str, Any], path: str) -> Any:  # Any: значение поля JSON
    """Значение по пути ``payload.a.b``.

    Raises:
        MappingError: путь не из ``payload`` или поля нет.
    """
    root, _, rest = path.partition(".")
    if root != PAYLOAD_ROOT or not rest:
        raise MappingError(f"путь вне payload: {path}")
    current: Any = payload
    for part in rest.split("."):
        if not isinstance(current, Mapping) or part not in current:
            raise MappingError(f"в событии нет поля {path}")
        current = current[part]
    return current


def _same(expected: FilterValue, actual: object) -> bool:
    # bool - подкласс int: `True == 1`, но фильтр `true` не должен пропускать число 1.
    return type(expected) is type(actual) and expected == actual


def matches_filter(condition: Mapping[str, FilterValue], payload: Mapping[str, Any]) -> bool:
    """Все поля фильтра равны полям payload (пустой фильтр - любое событие)."""
    return all(key in payload and _same(value, payload[key]) for key, value in condition.items())


def render(template: str, values: Mapping[str, str]) -> str:
    """Подставить ``{имя}`` за один проход: скобки внутри значений не раскрываются.

    Плейсхолдер без значения остаётся как есть (конфиг проверен при старте).
    """
    return PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), template)


def _text(value: Any) -> str:  # Any: значение поля JSON
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)[:PARAM_MAX]


def _dedup_key(template: str, payload: Mapping[str, Any]) -> str:
    def part(match: Any) -> str:  # re.Match
        value = resolve_path(payload, match.group(1))
        if value is None:
            raise MappingError(f"поле {match.group(1)} для dedup_key пусто")
        return str(value)

    key = DEDUP_PLACEHOLDER.sub(part, template)
    if len(key) > DEDUP_KEY_MAX:
        raise MappingError("dedup_key длиннее допустимого")
    return key


def map_event(
    notification_type: NotificationType,
    payload: Mapping[str, Any],
    *,
    locale: str,
    default_locale: str,
    user_id: UUID | None = None,
) -> Candidate:
    """Собрать уведомление из события по типу.

    Args:
        notification_type: Тип из конфига.
        payload: ``payload`` события.
        locale: Желаемая локаль; нет шаблона - ``default_locale``.
        default_locale: Локаль по умолчанию (шаблон есть всегда, проверено при старте).
        user_id: Получатель типа с ``audience`` (у типа с ``recipient`` берётся из события).

    Raises:
        MappingError: события не хватает полей, получатель не UUID, ключ слишком длинный.
    """
    if notification_type.recipient is not None:
        raw_recipient = resolve_path(payload, notification_type.recipient)
        try:
            user_id = UUID(str(raw_recipient))
        except ValueError as exc:
            raise MappingError("получатель не является UUID") from exc
    elif user_id is None:
        raise MappingError("для типа с audience нужен user_id получателя")
    values = {
        name: _text(resolve_path(payload, path)) for name, path in notification_type.params.items()
    }
    for name, lookup in notification_type.lookups.items():
        code = _text(resolve_path(payload, lookup.path))
        values[name] = lookup.resolve(code, locale, default_locale)
    template = (
        notification_type.templates.get(locale) or notification_type.templates[default_locale]
    )
    return Candidate(
        user_id=user_id,
        type_key=notification_type.key,
        dedup_key=_dedup_key(notification_type.dedup_key, payload),
        title=render(template.title, values)[:TITLE_MAX],
        body=render(template.body, values)[:BODY_MAX],
        data={
            name: _text(resolve_path(payload, path))
            for name, path in notification_type.data.items()
        },
    )
