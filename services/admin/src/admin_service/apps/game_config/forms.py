"""Формы раздела «Режимы». Поля запросов — по docs/plans/stage-2.md §2.6 (openapi game)."""

from __future__ import annotations

import re
from typing import Any

from django import forms
from django.conf import settings

from admin_service.apps.game_config import config_text
from admin_service.core.forms import MutationForm

DOMAINS = [("", "все"), ("music", "музыка"), ("film", "кино")]
STATUSES = [
    ("", "все"),
    ("draft", "не опубликован"),
    ("published", "опубликован"),
    ("archived", "в архиве"),
]
FORMAT_CHOICES = [("yaml", "YAML"), ("json", "JSON")]
# Как ModeKeyPath в contracts/openapi/game.json.
MODE_KEY_RE = re.compile(r"^(music|film)\.[a-z0-9_]+\.[a-z0-9_]+$")
MODE_KEY_MAX = 100
REASON_MIN = 3  # как в MutationForm и ``reason`` запросов game


class ModeFilterForm(forms.Form):
    """Фильтры списка режимов."""

    domain = forms.ChoiceField(label="Домен", choices=DOMAINS, required=False)
    status = forms.ChoiceField(label="Статус", choices=STATUSES, required=False)


class NewModeForm(forms.Form):
    """Ключ нового режима: черновик создаётся первым сохранением в редакторе."""

    key = forms.RegexField(
        label="Ключ",
        regex=MODE_KEY_RE,
        max_length=MODE_KEY_MAX,
        help_text="домен.тип.имя, например music.daily.classic",
        error_messages={"invalid": "формат: music|film.<тип>.<имя> (a-z, 0-9, _)"},
    )


class EditorForm(forms.Form):
    """Текст черновика и общие поля сохранения.

    ``base_revision`` — ревизия, от которой правили (пусто — черновика ещё нет): game
    отвергнет сохранение поверх чужой правки (409 ``game.draft_conflict``). Причина нужна
    только для сохранения, не для проверки, поэтому здесь необязательна (см. ``clean``).
    """

    fmt = forms.ChoiceField(label="Формат", choices=FORMAT_CHOICES, initial="yaml")
    text = forms.CharField(
        label="Конфиг",
        strip=False,
        widget=forms.Textarea(
            {"rows": 32, "cols": 100, "spellcheck": "false", "class": "gv-code", "wrap": "off"}
        ),
    )
    base_revision = forms.IntegerField(required=False, min_value=0, widget=forms.HiddenInput)
    reason = forms.CharField(
        label="Причина (для сохранения)",
        required=False,
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
        self.parse_error: config_text.ConfigTextError | None = None

    def clean_text(self) -> str:
        text: str = self.cleaned_data["text"]
        limit = settings.GV_MODE_CONFIG_MAX_BYTES
        if len(text.encode("utf-8")) > limit:
            raise forms.ValidationError(f"конфиг больше {limit} байт")
        return text

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        text, fmt = data.get("text"), data.get("fmt")
        if text is not None and fmt in config_text.FORMATS:
            try:
                self.body = config_text.parse(text, fmt)
            except config_text.ConfigTextError as exc:
                self.parse_error = exc
                where = f"строка {exc.line}: " if exc.line else ""
                self.add_error("text", f"{where}{exc.message}")
        return data

    def require_reason(self) -> bool:
        """Проверить причину перед сохранением (3..500 символов, как в контракте)."""
        reason = self.cleaned_data.get("reason") or ""
        if len(reason) < REASON_MIN:
            self.add_error("reason", f"укажите причину (от {REASON_MIN} символов)")
            return False
        return True


class PublishForm(MutationForm):
    """Публикация черновика ревизии ``draft_revision``."""

    draft_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)


class RollbackForm(MutationForm):
    """Откат к опубликованной версии.

    Список версий — только подсказка в выпадающем списке: существование версии проверяет
    game (404), чтобы не делать лишний запрос при отправке формы.
    """

    to_version = forms.IntegerField(label="К версии", min_value=1, widget=forms.Select)

    def __init__(self, *args: Any, versions: list[int] | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        widget = self.fields["to_version"].widget
        assert isinstance(widget, forms.Select)  # noqa: S101 — объявлено выше
        widget.choices = [(v, f"v{v}") for v in versions or []]


class ArchiveForm(MutationForm):
    """Архив режима (из каталога пропадает, начатые сессии доигрываются)."""


class AssetRequestForm(MutationForm):
    """«Запросить ассеты» под загадку черновика ревизии ``draft_revision`` (ADR 0025)."""

    draft_revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)
