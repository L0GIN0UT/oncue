"""Формы раздела «Каталог». Поля запросов — строго по docs/plans/stage-1.md §2.5."""

from __future__ import annotations

from typing import Any

from django import forms
from django.conf import settings

from admin_service.core.forms import MutationForm

DOMAINS = [("", "все"), ("film", "фильмы"), ("music", "музыка")]
STATUSES = [("", "все"), ("active", "активные"), ("blocked", "заблокированные")]
ALIAS_KINDS = ("title", "alt", "translit", "short")
IMPORT_SOURCES = [
    ("tmdb", "TMDB (id фильма)"),
    ("file", "Файл (file_id)"),
    ("musicbrainz", "MusicBrainz"),
    ("spotify", "Spotify"),
    ("yandex_music", "Яндекс Музыка (только метаданные)"),
]
SOURCE_KINDS = [("audio", "аудио"), ("frame", "кадр"), ("poster", "постер")]
MAX_ALIASES = 100
MAX_ALIAS_LEN = 300  # AliasIn.text в contracts/openapi/catalog.json
MAX_LANG_LEN = 8
_LANG, _KIND = 1, 2  # позиции полей в строке «текст | язык | вид»


class SearchForm(forms.Form):
    """Фильтры списка единиц каталога."""

    q = forms.CharField(label="Поиск", required=False, max_length=64)
    domain = forms.ChoiceField(label="Домен", choices=DOMAINS, required=False)
    status = forms.ChoiceField(label="Статус", choices=STATUSES, required=False)
    cursor = forms.CharField(required=False, max_length=1024, widget=forms.HiddenInput)


class ReasonForm(MutationForm):
    """Блокировка, разблокировка, перегенерация: только причина."""


class AliasesForm(MutationForm):
    """Полная замена алиасов: строка = ``текст | язык | вид`` (язык и вид необязательны)."""

    aliases = forms.CharField(
        label="Алиасы",
        widget=forms.Textarea({"rows": 8}),
        help_text="По одному в строке: текст | язык | вид (title, alt, translit, short)",
    )

    def clean_aliases(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        lines = [line.strip() for line in self.cleaned_data["aliases"].splitlines()]
        for number, line in enumerate((x for x in lines if x), start=1):
            parts = [p.strip() for p in line.split("|")]
            text = parts[0]
            lang = parts[_LANG] if len(parts) > _LANG and parts[_LANG] else None
            kind = parts[_KIND] if len(parts) > _KIND and parts[_KIND] else "alt"
            if not text or len(text) > MAX_ALIAS_LEN:
                raise forms.ValidationError(
                    f"строка {number}: текст от 1 до {MAX_ALIAS_LEN} символов"
                )
            if kind not in ALIAS_KINDS:
                raise forms.ValidationError(
                    f"строка {number}: вид — один из {', '.join(ALIAS_KINDS)}"
                )
            if lang is not None and len(lang) > MAX_LANG_LEN:
                raise forms.ValidationError(f"строка {number}: код языка не длиннее 8 символов")
            result.append({"text": text, "lang": lang, "kind": kind})
        if not result:
            raise forms.ValidationError("нужен хотя бы один алиас")
        if len(result) > MAX_ALIASES:
            raise forms.ValidationError(f"не больше {MAX_ALIASES} алиасов")
        return result


class ImportForm(MutationForm):
    """Запуск импорта: ``{source, ref | file_id}``."""

    source = forms.ChoiceField(label="Источник", choices=IMPORT_SOURCES)
    ref = forms.CharField(label="Идентификатор / ссылка", required=False, max_length=200)
    # Пределы — как в ImportRequest контракта catalog: длиннее сервис отвергнет 422.
    file_id = forms.CharField(label="file_id (для импорта файлом)", required=False, max_length=100)

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        source = data.get("source")
        if source == "file":
            if not data.get("file_id"):
                self.add_error("file_id", "для импорта файлом нужен file_id")
        elif source and not data.get("ref"):
            self.add_error("ref", "укажите идентификатор в источнике")
        return data

    def payload(self) -> dict[str, str]:
        """Тело запроса ``POST /admin/v1/imports`` (без причины — она отдельным полем)."""
        data = self.cleaned_data
        if data["source"] == "file":
            return {"source": "file", "file_id": data["file_id"]}
        return {"source": data["source"], "ref": data["ref"]}


class SourceUploadForm(MutationForm):
    """Загрузка исходника (аудио, кадр, постер) в media-pipeline."""

    kind = forms.ChoiceField(label="Тип", choices=SOURCE_KINDS)
    file = forms.FileField(label="Файл")

    def clean_file(self) -> Any:
        upload = self.cleaned_data["file"]
        limit = settings.GV_MAX_UPLOAD_MB * 1024 * 1024
        if upload.size > limit:
            raise forms.ValidationError(f"файл больше {settings.GV_MAX_UPLOAD_MB} МБ")
        return upload
