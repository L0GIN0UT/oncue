"""Формы раздела «Прогрессия». Поля запросов — по contracts/openapi/progression.json (admin)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django import forms

from admin_service.core.forms import MutationForm

AMOUNT_MAX = 100_000_000  # как `admin.grant_max_amount` в схеме конфига; точный лимит - в сервисе


class PublishForm(MutationForm):
    """Публикация черновика ``version`` с хешем тела, который админ видел на экране."""

    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    body_sha256 = forms.RegexField(regex=r"^[0-9a-f]{64}$", widget=forms.HiddenInput)


class RollbackForm(MutationForm):
    """Откат к ранее действовавшей версии.

    Список версий - подсказка выпадающего списка; существование и право отката проверяет сервис.
    """

    to_version = forms.IntegerField(label="К версии", min_value=1, widget=forms.Select)

    def __init__(self, *args: Any, versions: list[int] | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        widget = self.fields["to_version"].widget
        assert isinstance(widget, forms.Select)  # noqa: S101 - объявлено выше
        widget.choices = [(v, f"v{v}") for v in versions or []]


class LedgerSearchForm(forms.Form):
    """Поиск журнала по идентификатору игрока."""

    user_id = forms.UUIDField(label="ID игрока")


class GrantForm(MutationForm):
    """Ручная выдача Версов: сумма, бакет и обязательная причина (лимиты проверяет сервис)."""

    amount = forms.IntegerField(label="Сумма, Версы", min_value=1, max_value=AMOUNT_MAX)


class ReverseForm(MutationForm):
    """Сторно проводки ``op_id`` (причина обязательна)."""

    op_id = forms.CharField(max_length=200, widget=forms.HiddenInput)


def parse_user_id(raw: str | None) -> UUID | None:
    """Идентификатор игрока из строки запроса или ``None``."""
    try:
        return UUID(str(raw)) if raw else None
    except ValueError:
        return None
