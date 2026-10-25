"""Формы раздела «Рассылки». Поля — по ``BroadcastRequest`` contracts/openapi/notifications.json."""

from __future__ import annotations

import re

from django import forms

from admin_service.core.forms import MutationForm

SUBJECT_MAX = 150
BODY_MAX = 5000
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def validate_subject(value: str) -> None:
    """Тема - одна строка без управляющих символов (в заголовке письма они опасны)."""
    if _CONTROL.search(value):
        raise forms.ValidationError(
            "В теме не должно быть переводов строки и управляющих символов", code="control_chars"
        )


class TypeToggleForm(MutationForm):
    """Переключатель типа уведомлений: ключ типа, целевое состояние и причина (в аудит)."""

    type = forms.RegexField(regex=r"^[a-z][a-z0-9_]*$", max_length=64)
    enabled = forms.BooleanField(required=False)


class BroadcastForm(MutationForm):
    """Рассылка: тема, текст и причина (причина уходит в аудит notifications)."""

    subject = forms.CharField(
        label="Тема", min_length=1, max_length=SUBJECT_MAX, validators=[validate_subject]
    )
    body = forms.CharField(
        label="Текст", min_length=1, max_length=BODY_MAX, widget=forms.Textarea({"rows": 8})
    )
