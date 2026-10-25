"""Формы раздела «Модерация». Поля запросов - по contracts/openapi/social.json (admin-API)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from django import forms

from admin_service.core.forms import MutationForm

KINDS = [
    ("", "все"),
    ("player", "игрок"),
    ("collection", "сборник"),
    ("comment", "комментарий"),
    ("avatar", "аватарка"),
]
STATUSES = [
    ("", "все"),
    ("open", "открыт"),
    ("in_work", "в работе"),
    ("escalated", "эскалирован"),
    ("resolved", "решён"),
]
DECISIONS = [
    ("dismissed", "отклонить жалобы"),
    ("content_removed", "удалить контент"),
    ("sanctioned", "санкция игроку"),
]
SANCTION_KINDS = [
    ("ranked", "рейтинговые игры"),
    ("chat", "чат и комментарии"),
    ("online", "онлайн"),
    ("account", "аккаунт (только старший)"),
]
DURATION_MAX = 315_360_000  # как в SanctionGrant контракта; точные пределы - конфиг social
CLAIM_REASON = "взят в работу"


class CaseFilterForm(forms.Form):
    """Фильтры очереди."""

    status = forms.ChoiceField(label="Статус", choices=STATUSES, required=False)
    target_kind = forms.ChoiceField(label="Объект", choices=KINDS, required=False)
    mine = forms.BooleanField(label="Только мои", required=False)


WIN_TRADING_KINDS = [
    ("", "все"),
    ("pair_repeat", "повторы пары"),
    ("forfeit_series", "сдачи подряд против одного"),
    ("forfeit_window", "сдачи за окно"),
]
SEVERITIES = [("", "любая"), ("high", "высокая"), ("medium", "средняя")]


class WinTradingFilterForm(forms.Form):
    """Фильтры очереди сигналов win-trading."""

    kind = forms.ChoiceField(label="Сигнал", choices=WIN_TRADING_KINDS, required=False)
    severity = forms.ChoiceField(label="Серьёзность", choices=SEVERITIES, required=False)
    user_id = forms.UUIDField(label="Игрок (ID)", required=False)


class ClaimForm(MutationForm):
    """Взять кейс: причина фиксирована (аудит), версия кейса - из карточки."""

    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    reason = forms.CharField(initial=CLAIM_REASON, widget=forms.HiddenInput)


class EscalateForm(MutationForm):
    """Эскалировать кейс старшему модератору (причина обязательна)."""

    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)


class ResolveForm(MutationForm):
    """Решение по кейсу; для ``sanctioned`` нужна санкция (вид, срок, причина санкции)."""

    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
    decision = forms.ChoiceField(label="Решение", choices=DECISIONS)
    sanction_kind = forms.ChoiceField(
        label="Санкция", choices=[("", "-"), *SANCTION_KINDS], required=False
    )
    sanction_duration_s = forms.IntegerField(
        label="Срок, секунд (пусто - бессрочно)",
        required=False,
        min_value=60,
        max_value=DURATION_MAX,
    )
    sanction_reason = forms.CharField(
        label="Причина санкции (увидит игрок)", required=False, min_length=3, max_length=500
    )

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        sanctioned = data.get("decision") == "sanctioned"
        if sanctioned and not (data.get("sanction_kind") and data.get("sanction_reason")):
            raise forms.ValidationError("Для санкции укажите вид и причину санкции")
        if not sanctioned and (data.get("sanction_kind") or data.get("sanction_duration_s")):
            raise forms.ValidationError("Санкцию можно задать только при решении «санкция»")
        return data

    def sanction(self) -> dict[str, Any] | None:
        """Тело ``sanction`` запроса или ``None``."""
        data = self.cleaned_data
        if data.get("decision") != "sanctioned":
            return None
        return {
            "kind": data["sanction_kind"],
            "duration_s": data.get("sanction_duration_s"),
            "reason": data["sanction_reason"],
        }


class GrantSanctionForm(MutationForm):
    """Выдача санкции игроку (срок пуст - бессрочно; пределы проверяет сервис)."""

    kind = forms.ChoiceField(label="Санкция", choices=SANCTION_KINDS)
    duration_s = forms.IntegerField(
        label="Срок, секунд (пусто - бессрочно)",
        required=False,
        min_value=60,
        max_value=DURATION_MAX,
    )


class RevokeSanctionForm(MutationForm):
    """Снять санкцию (``sanction_id`` приходит скрытым полем из карточки игрока)."""

    sanction_id = forms.UUIDField(widget=forms.HiddenInput)


class PlayerSearchForm(forms.Form):
    """Поиск игрока по идентификатору."""

    user_id = forms.UUIDField(label="ID игрока")


def parse_uuid(raw: str | None) -> UUID | None:
    """UUID из строки запроса или ``None``."""
    try:
        return UUID(str(raw)) if raw else None
    except ValueError:
        return None
