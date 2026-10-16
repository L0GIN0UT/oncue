"""Импорт из файла JSONL (``FileImportProvider``). Формат — ``seeds/catalog/README.md``.

Этап 3a: строка может содержать ``countries``, ``clue_texts`` (тексты загадок, ADR 0024) и
``source_media`` (исходники с ``position``/``source_url``/``license_note``, D6).
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from catalog_service.application.dto import ImportedClueTexts, ImportedItem, PersonRef
from catalog_service.domain.model import (
    AliasDraft,
    AliasKind,
    ExternalSource,
    MediaKind,
    MediaMetrics,
    PersonRole,
    SourceMediaDraft,
    SourceMediaKind,
    SourceMediaOrigin,
)
from gv_core.errors import AppError, InvalidInputError, NotFoundError

FILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,62}\.jsonl$")
MAX_LINE_BYTES = 64 * 1024


class _Alias(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=300)
    kind: Literal["title", "alt", "translit", "short"] = "alt"
    lang: str | None = Field(default=None, max_length=8)


class _Person(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    role: Literal["artist", "director", "actor"]
    key: str | None = Field(default=None, max_length=190)


class _Metrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    popularity: float | None = Field(default=None, ge=0)
    vote_average: float | None = Field(default=None, ge=0, le=10)
    vote_count: int | None = Field(default=None, ge=0)
    revenue_usd: int | None = Field(default=None, ge=0)
    runtime_min: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)


class _ClueTexts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plot_raw: str | None = Field(default=None, min_length=1, max_length=5000)
    plot_masked: str | None = Field(default=None, min_length=1, max_length=2000)
    tagline: str | None = Field(default=None, min_length=1, max_length=300)
    emoji: str | None = Field(default=None, min_length=1, max_length=64)


class _SourceMedia(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["audio", "frame", "poster", "soundtrack", "audio_clip"]
    origin: Literal["upload", "tmdb"]
    uri: str = Field(min_length=1, max_length=2000, pattern=r"^https://")
    position: int = Field(default=1, ge=1, le=100)
    source_url: str | None = Field(default=None, min_length=1, max_length=2000)
    license_note: str | None = Field(default=None, min_length=1, max_length=500)


class FileLine(BaseModel):
    """Строка файла импорта."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=190, pattern=r"^[a-z0-9][a-z0-9_:\-.]*$")
    kind: Literal["film", "track"]
    title: str = Field(min_length=1, max_length=300)
    original_title: str | None = Field(default=None, max_length=300)
    year: int | None = None
    aliases: list[_Alias] = Field(default_factory=list, max_length=50)
    persons: list[_Person] = Field(default_factory=list, max_length=50)
    genres: list[str] = Field(default_factory=list, max_length=20)
    external_refs: dict[Literal["tmdb", "musicbrainz", "isrc", "spotify", "yandex_music"], str] = (
        Field(default_factory=dict)
    )
    metrics: _Metrics = Field(default_factory=_Metrics)
    countries: list[str] = Field(default_factory=list, max_length=20)
    clue_texts: _ClueTexts | None = None
    source_media: list[_SourceMedia] = Field(default_factory=list, max_length=20)

    def to_imported(self, *, texts_approved: bool = False) -> ImportedItem:
        """В DTO импорта. Ключ строки становится внешним id ``seed``.

        Args:
            texts_approved: Тексты загадок сразу одобрены (засев local/ci).
        """
        role_order: dict[str, int] = {}
        persons: list[PersonRef] = []
        for p in self.persons:
            order = role_order.get(p.role, 0)
            role_order[p.role] = order + 1
            key = f"seed:{p.key}" if p.key else f"name:{p.name.casefold()}"
            persons.append(PersonRef(key, p.name, PersonRole(p.role), order))
        refs = {ExternalSource(k): v for k, v in self.external_refs.items()}
        refs[ExternalSource.SEED] = self.key
        return ImportedItem(
            kind=MediaKind(self.kind),
            title=self.title,
            original_title=self.original_title,
            year=self.year,
            aliases=tuple(AliasDraft(a.text, AliasKind(a.kind), a.lang) for a in self.aliases),
            persons=tuple(persons),
            genres=tuple(self.genres),
            external_refs=refs,
            metrics=MediaMetrics(**self.metrics.model_dump()),
            countries=tuple(self.countries),
            clue_texts=ImportedClueTexts(**self.clue_texts.model_dump())
            if self.clue_texts
            else None,
            texts_authoritative=texts_approved and self.clue_texts is not None,
            source_media=tuple(
                SourceMediaDraft(
                    SourceMediaKind(m.kind),
                    SourceMediaOrigin(m.origin),
                    m.uri,
                    position=m.position,
                    source_url=m.source_url,
                    license_note=m.license_note,
                )
                for m in self.source_media
            ),
        )

    def missing_provenance(self) -> bool:
        """У какого-то исходника нет ``source_url`` или ``license_note`` (D6)."""
        return any(not (m.source_url and m.license_note) for m in self.source_media)


class FileImportProvider:
    """``ImportFileReader``: файлы JSONL из каталога ``GV_IMPORTS__FILES_DIR``.

    ``file_id`` — только имя файла (``films.jsonl``) без путей: выход за пределы каталога
    невозможен (проверка формата и ``resolve`` родителя).
    """

    def __init__(
        self,
        files_dir: Path,
        *,
        max_lines: int = 10_000,
        texts_approved: bool = False,
        require_provenance: bool = False,
    ) -> None:
        self._dir = files_dir
        self._max_lines = max_lines
        self._texts_approved = texts_approved
        self._require_provenance = require_provenance

    def _path(self, file_id: str) -> Path:
        if not FILE_ID_RE.match(file_id):
            raise InvalidInputError("недопустимый file_id", code="catalog.import_bad_ref")
        root = self._dir.resolve()
        path = (root / file_id).resolve()
        if path.parent != root or not path.is_file():
            raise NotFoundError("файл импорта не найден", code="catalog.import_file_not_found")
        return path

    async def read(self, file_id: str) -> AsyncIterator[ImportedItem | ValueError]:
        """Строки файла; ошибка строки не прерывает чтение."""
        path = self._path(file_id)
        lines = await asyncio.to_thread(path.read_text, "utf-8")
        for number, raw in enumerate(lines.splitlines(), start=1):
            if number > self._max_lines:
                yield ValueError(f"строка {number}: превышен лимит строк {self._max_lines}")
                return
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            yield parse_line(
                line,
                number,
                texts_approved=self._texts_approved,
                require_provenance=self._require_provenance,
            )


def parse_line(
    line: str, number: int, *, texts_approved: bool = False, require_provenance: bool = False
) -> ImportedItem | ValueError:
    """Разобрать строку; ошибка — ``ValueError`` с номером строки (без содержимого).

    Args:
        texts_approved: Тексты загадок строки сразу одобрены (``GV_CATALOG__SEED_TEXTS_APPROVED``).
        require_provenance: Исходник без ``source_url``/``license_note`` — ошибка строки
            (вне local/ci, D6).
    """
    if len(line.encode()) > MAX_LINE_BYTES:
        return ValueError(f"строка {number}: слишком длинная")
    try:
        parsed = FileLine.model_validate(json.loads(line))
    except json.JSONDecodeError:
        return ValueError(f"строка {number}: не JSON")
    except ValidationError as exc:
        fields = sorted({".".join(map(str, e["loc"])) for e in exc.errors()})
        return ValueError(f"строка {number}: неверные поля {fields}")
    if require_provenance and parsed.missing_provenance():
        return ValueError(f"строка {number}: у исходника нет source_url или license_note")
    try:
        return parsed.to_imported(texts_approved=texts_approved)
    except AppError as exc:  # позиция/страна вне домена — ошибка строки, а не импорта
        return ValueError(f"строка {number}: {exc.code}")
