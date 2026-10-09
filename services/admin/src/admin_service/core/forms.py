"""Формы ядра: вход и общие поля мутаций (причина, ключ идемпотентности)."""

from __future__ import annotations

from typing import Any

from django import forms
from django.contrib.admin.forms import AdminAuthenticationForm

from admin_service.core.auth import IDENTITY_ERROR_ATTR
from gv_core.ids import uuid7


class IdentityLoginForm(AdminAuthenticationForm):
    """Вход по e-mail или никнейму и паролю identity."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["username"].label = "E-mail или никнейм"
        self.fields["username"].widget.attrs["maxlength"] = 254

    def clean(self) -> dict[str, Any]:
        try:
            return super().clean()
        except forms.ValidationError:
            unavailable = getattr(self.request, IDENTITY_ERROR_ATTR, None)
            if unavailable:
                raise forms.ValidationError(
                    f"Вход временно невозможен: {unavailable}", code="identity_unavailable"
                ) from None
            raise


class MutationForm(forms.Form):
    """Основа формы действия через admin-API: обязательная причина и ключ идемпотентности.

    Ключ генерируется при показе формы и приходит обратно скрытым полем: повторная отправка
    той же формы (двойной клик, F5) — тот же ключ, сервис не выполнит действие дважды.
    """

    reason = forms.CharField(
        label="Причина",
        min_length=3,
        max_length=500,
        strip=True,
        widget=forms.Textarea({"rows": 2}),
    )
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.initial.setdefault("idempotency_key", str(uuid7()))
