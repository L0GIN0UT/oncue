"""Формы раздела «События». Поля запросов — по contracts/openapi/progression.json (admin)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from django import forms

from admin_service.core.forms import MutationForm

_LOCAL_FORMATS = ["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"]


def _utc_field(label: str) -> forms.DateTimeField:
    return forms.DateTimeField(
        label=f"{label} (UTC)",
        input_formats=_LOCAL_FORMATS,
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
    )


def to_utc_iso(value: datetime) -> str:
    """Момент как UTC ISO-8601: введённое время считается UTC, часовой пояс не угадываем."""
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat().replace("+00:00", "Z")


class EventKeyForm(forms.Form):
    """Ключ нового события (неизменяем)."""

    key = forms.RegexField(
        label="Ключ (a-z, цифры, _)",
        regex=r"^[a-z][a-z0-9_]{0,63}$",
        max_length=64,
        error_messages={"invalid": "Ключ: строчные латинские буквы, цифры и _, начинается с буквы"},
    )


class RunForm(MutationForm):
    """Новый draft-запуск (и перезапуск): период; пересечения проверяет сервис при публикации."""

    starts_at = _utc_field("Начало")
    ends_at = _utc_field("Конец")

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        start, end = data.get("starts_at"), data.get("ends_at")
        if start and end and end <= start:
            raise forms.ValidationError("Конец периода должен быть позже начала")
        return data


class PublishRunForm(MutationForm):
    """Публикация запуска: подтверждение привязано к хешу определения, который видел админ."""

    body_sha256 = forms.RegexField(regex=r"^[0-9a-f]{64}$", widget=forms.HiddenInput)
    confirm = forms.BooleanField(
        label="Подтверждаю публикацию этого определения (после неё оно не меняется)"
    )


class StopRunForm(MutationForm):
    """Остановка запуска (причина обязательна)."""


class MapForm(MutationForm):
    """Загрузка карты; размер проверяется до обращения к сервису."""

    file = forms.FileField(label="Карта (PNG или WebP, без анимации)")

    def __init__(self, *args: Any, limit_bytes: int, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.limit_bytes = limit_bytes

    def clean_file(self) -> Any:
        upload = self.cleaned_data["file"]
        if upload.size > self.limit_bytes:
            raise forms.ValidationError(
                f"Файл {upload.size} байт больше допустимых {self.limit_bytes} байт"
            )
        return upload


class FindsMetaForm(forms.Form):
    """Служебные поля режима формой: причина (для сохранения) и ключ идемпотентности."""

    reason = forms.CharField(required=False, max_length=500, strip=True)
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            from gv_core.ids import uuid7

            self.initial.setdefault("idempotency_key", str(uuid7()))

    def require_reason(self) -> bool:
        if len(self.cleaned_data.get("reason") or "") < 3:  # noqa: PLR2004 - как MutationForm
            self.add_error("reason", "укажите причину (от 3 символов)")
            return False
        return True
