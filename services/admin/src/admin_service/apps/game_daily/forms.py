"""Формы раздела «Наборы дня». Поля запросов — по docs/plans/stage-2.md §2.6."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from django import forms
from django.conf import settings

from admin_service.apps.game_config.forms import MODE_KEY_MAX, MODE_KEY_RE
from admin_service.core.forms import MutationForm

SEARCH_MAX = 64  # q в GET /admin/v1/projection/items
CANDIDATES_LIMIT = 20  # limit там же (по умолчанию 20, максимум 50)


class CalendarForm(forms.Form):
    """Режим и диапазон дней календаря (оба края включительно)."""

    mode_key = forms.RegexField(
        label="Режим",
        regex=MODE_KEY_RE,
        max_length=MODE_KEY_MAX,
        error_messages={"invalid": "формат: music|film.<тип>.<имя>"},
        widget=forms.TextInput({"list": "gv-mode-keys", "size": 40}),
    )
    start = forms.DateField(label="С", required=False, widget=forms.DateInput({"type": "date"}))
    end = forms.DateField(label="По", required=False, widget=forms.DateInput({"type": "date"}))

    def resolve(self, today: date) -> tuple[date, date]:
        """Диапазон с умолчаниями: с сегодняшнего дня на ``GV_DAILY_DEFAULT_DAYS`` дней."""
        start: date = self.cleaned_data.get("start") or today
        end: date = self.cleaned_data.get("end") or start + timedelta(
            days=settings.GV_DAILY_DEFAULT_DAYS - 1
        )
        return start, end

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        start, end = data.get("start"), data.get("end")
        if start and end:
            if end < start:
                self.add_error("end", "конец диапазона раньше начала")
            elif (end - start).days + 1 > settings.GV_DAILY_RANGE_MAX_DAYS:
                self.add_error(
                    "end", f"не больше {settings.GV_DAILY_RANGE_MAX_DAYS} дней за один запрос"
                )
        return data


class CandidateSearchForm(forms.Form):
    """Поиск кандидата на замену в проекции game."""

    q = forms.CharField(label="Поиск", max_length=SEARCH_MAX, required=False)


class ReplaceItemForm(MutationForm):
    """Замена единицы набора: выбранный кандидат и причина."""

    media_item_id = forms.UUIDField(label="Новая единица (id)")


class RegenerateForm(MutationForm):
    """Перегенерация набора: только причина."""
