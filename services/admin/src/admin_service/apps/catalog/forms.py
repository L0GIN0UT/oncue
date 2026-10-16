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
# Виды и происхождение исходника — как Body_upload_source в contracts/openapi/media_pipeline.json.
SOURCE_KINDS = [
    ("audio", "аудио (трек)"),
    ("frame", "кадр"),
    ("poster", "постер"),
    ("soundtrack", "саундтрек фильма"),
    ("audio_clip", "звук фильма"),
]
SOURCE_ORIGINS = [("", "—"), ("upload", "своя загрузка"), ("tmdb", "TMDB"), ("seed", "засев")]
POSITION_MIN, POSITION_MAX = 1, 100
SOURCE_URL_MAX, LICENSE_NOTE_MAX = 2000, 500
# Где происхождение исходника необязательно (как у media-pipeline, D6 плана этапа 3a).
PROVENANCE_OPTIONAL_ENVS = frozenset({"local", "ci"})
# Пределы текстов загадок — как ClueTextsIn в contracts/openapi/catalog.json.
PLOT_MAX, TAGLINE_MAX, EMOJI_MAX = 2000, 300, 64
REPUBLISH_DOMAINS = [("", "все домены"), ("film", "фильмы"), ("music", "музыка")]
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
    """Загрузка исходника в media-pipeline: вид, порядок и происхождение (D6).

    Вне local/ci происхождение, ссылка на источник и лицензия обязательны — как у
    media-pipeline (иначе 422 ``source.provenance_required``): форма не отправит запрос зря.
    """

    kind = forms.ChoiceField(label="Тип", choices=SOURCE_KINDS)
    position = forms.IntegerField(
        label="Порядок",
        min_value=POSITION_MIN,
        max_value=POSITION_MAX,
        initial=POSITION_MIN,
        help_text="для кадров последовательности: 1 — самый сложный",
    )
    origin = forms.ChoiceField(label="Происхождение", choices=SOURCE_ORIGINS, required=False)
    source_url = forms.URLField(
        label="Ссылка на источник",
        required=False,
        max_length=SOURCE_URL_MAX,
        assume_scheme="https",
    )
    license_note = forms.CharField(
        label="Лицензия / основание", required=False, max_length=LICENSE_NOTE_MAX
    )
    file = forms.FileField(label="Файл")

    def clean_file(self) -> Any:
        upload = self.cleaned_data["file"]
        limit = settings.GV_MAX_UPLOAD_MB * 1024 * 1024
        if upload.size > limit:
            raise forms.ValidationError(f"файл больше {settings.GV_MAX_UPLOAD_MB} МБ")
        return upload

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if settings.GV_ENVIRONMENT not in PROVENANCE_OPTIONAL_ENVS:
            for name in ("origin", "source_url", "license_note"):
                if not data.get(name) and name not in self.errors:
                    self.add_error(name, "обязательно: без происхождения исходник не примут")
        return data

    def fields_payload(self, media_item_id: str) -> dict[str, str]:
        """Поля multipart-формы ``POST /admin/v1/sources`` (пустые не передаются)."""
        data = self.cleaned_data
        payload = {
            "media_item_id": media_item_id,
            "kind": data["kind"],
            "position": str(data["position"]),
            "reason": data["reason"],
        }
        for name in ("origin", "source_url", "license_note"):
            if data.get(name):
                payload[name] = str(data[name])
        return payload


class SourcePositionForm(MutationForm):
    """Смена порядка исходника среди своего вида (кадры ``frame_sequence``)."""

    source_media_id = forms.UUIDField(widget=forms.HiddenInput)
    position = forms.IntegerField(
        label="Новый порядок", min_value=POSITION_MIN, max_value=POSITION_MAX
    )


def _optional(value: Any) -> str | None:
    """Пустое поле текста загадки → ``None`` (в catalog — «очистить»)."""
    return value or None


class ClueTextsForm(MutationForm):
    """Правка текстов загадок: маскированный сюжет, слоган, эмодзи (ADR 0024).

    Исходный сюжет (``plot_raw``) не правится — он показывается рядом для сверки.
    ``base_revision`` — ревизия, которую видел модератор (409 при чужой правке).
    """

    plot_masked = forms.CharField(
        label="Сюжет для загадки (маскированный)",
        required=False,
        max_length=PLOT_MAX,
        widget=forms.Textarea({"rows": 10, "cols": 60}),
    )
    tagline = forms.CharField(label="Слоган", required=False, max_length=TAGLINE_MAX)
    emoji = forms.CharField(
        label="Эмодзи",
        required=False,
        max_length=EMOJI_MAX,
        help_text="до 12 эмодзи без пробелов",
    )
    base_revision = forms.IntegerField(min_value=0, widget=forms.HiddenInput)

    def texts(self) -> dict[str, str | None]:
        """Тексты для ``PUT clue-texts``: пустое поле — очистить."""
        data = self.cleaned_data
        return {name: _optional(data.get(name)) for name in ("plot_masked", "tagline", "emoji")}


class ApproveClueTextsForm(MutationForm):
    """Одобрение текстов ревизии ``revision``."""

    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)


class RepublishForm(MutationForm):
    """Переопубликовать единицы домена: тяжёлое действие, нужно явное подтверждение."""

    domain = forms.ChoiceField(label="Домен", choices=REPUBLISH_DOMAINS, required=False)
    confirm = forms.BooleanField(
        label="Подтверждаю: все единицы домена будут заново опубликованы событиями",
        required=True,
        error_messages={"required": "подтвердите переопубликацию"},
    )
