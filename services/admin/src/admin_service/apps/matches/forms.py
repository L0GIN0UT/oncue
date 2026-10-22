"""Формы раздела «Матчи». Поля запросов — по contracts/openapi/match.json (admin)."""

from __future__ import annotations

from django import forms

from admin_service.core.forms import MutationForm

# Фазы комнаты из контракта (AdminRoomCard.phase), кроме closed: список закрытые не отдаёт.
PHASES = [
    ("", "все"),
    ("lobby", "лобби"),
    ("countdown", "отсчёт"),
    ("level_open", "уровень открыт"),
    ("level_closed", "уровень закрыт"),
    ("reveal", "ревил"),
    ("summary", "итог"),
    ("paused", "пауза"),
]


class RoomFilterForm(forms.Form):
    """Фильтр списка активных комнат по фазе и курсор страницы."""

    phase = forms.ChoiceField(label="Фаза", choices=PHASES, required=False)
    # Курсор — непрозрачная строка сервиса: здесь только формат из контракта.
    cursor = forms.RegexField(
        regex=r"^[A-Za-z0-9_-]{1,512}$", required=False, widget=forms.HiddenInput
    )


class AbortForm(MutationForm):
    """Принудительное завершение комнаты: причина обязательна (3..500, как AbortRoomRequest)."""


class MatchLookupForm(forms.Form):
    """Открыть итог матча по идентификатору."""

    match_id = forms.UUIDField(label="Матч (id)")
