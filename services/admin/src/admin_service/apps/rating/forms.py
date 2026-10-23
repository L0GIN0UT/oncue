"""Формы раздела «Рейтинг». Поля запросов — по contracts/openapi/rating.json (admin)."""

from __future__ import annotations

from typing import Any

from django import forms

from admin_service.core.forms import MutationForm


class PublishForm(MutationForm):
    """Публикация версии-черновика ``version``."""

    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)


class RollbackForm(MutationForm):
    """Откат к ранее опубликованной версии.

    Список версий — подсказка выпадающего списка; существование версии проверяет rating (404).
    """

    to_version = forms.IntegerField(label="К версии", min_value=1, widget=forms.Select)

    def __init__(self, *args: Any, versions: list[int] | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        widget = self.fields["to_version"].widget
        assert isinstance(widget, forms.Select)  # noqa: S101 — объявлено выше
        widget.choices = [(v, f"v{v}") for v in versions or []]


class SeasonCreateForm(MutationForm):
    """Новый сезон; ``starts_at`` пусто — rating выберет момент сам."""

    starts_at = forms.DateTimeField(
        label="Начало (UTC, ГГГГ-ММ-ДД ЧЧ:ММ)",
        required=False,
        input_formats=["%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"],
    )


class SeasonCloseForm(MutationForm):
    """Закрытие сезона: необратимо, поэтому нужна явная отметка."""

    confirm = forms.BooleanField(
        label="Понимаю: сезон закроется сейчас, рейтинги сбросятся, места уйдут в событие"
    )


class RebuildForm(MutationForm):
    """Пересборка лидерборда текущего сезона."""
