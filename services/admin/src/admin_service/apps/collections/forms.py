"""Формы раздела «Сборники». Поля запросов — по contracts/openapi/collections.json (admin)."""

from __future__ import annotations

from typing import Any

from django import forms
from django.conf import settings

from admin_service.apps.collections import collection_text
from admin_service.apps.game_config.config_text import ConfigTextError
from admin_service.core.forms import MutationForm

STATUSES = [
    ("", "все"),
    ("draft", "черновик"),
    ("published", "опубликован"),
    ("hidden", "скрыт"),
]
KINDS = [("", "все"), ("ugc", "пользовательский"), ("curated", "кураторский")]
DOMAINS = [("film", "кино"), ("music", "музыка")]
SEARCH_MAX = 100  # q в GET /admin/v1/collections
RANK_MIN, RANK_MAX = 1, 1000  # rank в FeaturedRequest
REASON_MIN = 3  # как у MutationForm и ``reason`` запросов collections


class CollectionFilterForm(forms.Form):
    """Поиск по названию и нику автора, фильтры статуса и вида."""

    q = forms.CharField(label="Поиск", max_length=SEARCH_MAX, required=False, strip=True)
    status = forms.ChoiceField(label="Статус", choices=STATUSES, required=False)
    kind = forms.ChoiceField(label="Вид", choices=KINDS, required=False)
    # Курсор — непрозрачная строка сервиса: формат проверяет collections (422).
    cursor = forms.RegexField(
        regex=r"^[A-Za-z0-9_-]{1,512}$", required=False, widget=forms.HiddenInput
    )


class HideForm(MutationForm):
    """Скрыть или показать сборник (причина — в аудит collections и админки)."""


class FeaturedForm(MutationForm):
    """Отметка «рекомендуемое» и порядок в блоке (меньше — выше; пусто — в конец)."""

    featured = forms.BooleanField(label="Рекомендуемое", required=False)
    rank = forms.IntegerField(
        label="Порядок", required=False, min_value=RANK_MIN, max_value=RANK_MAX
    )


class RestrictionForm(MutationForm):
    """Ограничить автора сборника или снять ограничение."""

    restricted = forms.TypedChoiceField(
        choices=[("1", "ограничить"), ("0", "снять")],
        coerce=lambda v: v == "1",
        widget=forms.HiddenInput,
    )


class NewCuratedForm(forms.Form):
    """Домен нового кураторского сборника (заготовка редактора)."""

    domain = forms.ChoiceField(label="Домен", choices=DOMAINS)


class CuratedEditorForm(forms.Form):
    """YAML кураторского сборника и поля сохранения.

    ``base_version`` — версия, от которой правили (пусто — новый сборник): collections
    отвергнет сохранение поверх чужой правки (409 ``collections.version_conflict``).
    """

    text = forms.CharField(
        label="Сборник (YAML)",
        strip=False,
        widget=forms.Textarea(
            {"rows": 32, "cols": 100, "spellcheck": "false", "class": "gv-code", "wrap": "off"}
        ),
    )
    base_version = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput)
    reason = forms.CharField(
        label="Причина",
        min_length=REASON_MIN,
        max_length=500,
        strip=True,
        widget=forms.Textarea({"rows": 2}),
    )
    idempotency_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            from gv_core.ids import uuid7

            self.initial.setdefault("idempotency_key", str(uuid7()))
        self.body: dict[str, Any] | None = None

    def clean_text(self) -> str:
        text: str = self.cleaned_data["text"]
        limit = settings.GV_COLLECTION_TEXT_MAX_BYTES
        if len(text.encode("utf-8")) > limit:
            raise forms.ValidationError(f"текст больше {limit} байт")
        try:
            self.body = collection_text.parse(text)
        except ConfigTextError as exc:
            where = f"строка {exc.line}: " if exc.line else ""
            raise forms.ValidationError(f"{where}{exc.message}") from exc
        return text
