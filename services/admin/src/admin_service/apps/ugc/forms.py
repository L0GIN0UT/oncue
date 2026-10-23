"""Формы раздела «UGC-изображения». Поля запросов — по contracts/openapi/media_pipeline.json."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from django import forms

from admin_service.core.forms import MutationForm

STATUSES = ("pending", "approved", "rejected")


class ApproveForm(MutationForm):
    """Одобрение: только причина."""


class RejectForm(MutationForm):
    """Отклонение или снятие одобренного: код причины из справочника media и причина.

    Без справочника (настройки недоступны) код вводится текстом, проверит сервис.
    """

    reason_code = forms.RegexField(regex=r"^[a-z][a-z0-9_]*$", max_length=64, label="Код причины")

    def __init__(self, *args: Any, reasons: Sequence[tuple[str, str]] = (), **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if reasons:
            self.fields["reason_code"] = forms.ChoiceField(
                label="Код причины", choices=list(reasons)
            )
