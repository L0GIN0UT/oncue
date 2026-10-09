"""Адаптеры портов метаданных к единому порту ``ImportEntrySource``.

Каждый источник сам знает, какие ``ref``/``file_id`` он принимает и как превратить задание
в поток единиц; ``RequestImport``/``RunImport`` работают с реестром источников и по типу
источника не ветвятся. Реестр собирает composition root (``infrastructure.container``).
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

from catalog_service.application.dto import ImportedItem
from catalog_service.application.ports import (
    FilmMetadataProvider,
    ImportFileReader,
    MusicMetadataProvider,
)
from catalog_service.domain.import_job import ImportJob
from gv_core.errors import InvalidInputError

SEARCH_PREFIX = "search:"
FILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,62}\.jsonl$")
REF_RE = re.compile(r"^[A-Za-z0-9:_\-]{1,64}$")
MAX_SEARCH_LEN = 100


def _bad_ref(message: str) -> InvalidInputError:
    return InvalidInputError(message, code="catalog.import_bad_ref")


def _require_ref(ref: str | None, file_id: str | None) -> str:
    if file_id is not None or not ref:
        raise _bad_ref("нужен ref")
    return ref


def _plain_ref(ref: str) -> None:
    if not REF_RE.match(ref):
        raise _bad_ref("недопустимый ref")


def _job_ref(job: ImportJob) -> str:
    # Задание создаётся только после validate(): ref у него есть.
    assert job.ref is not None  # noqa: S101
    return job.ref


class FileEntrySource:
    """Импорт из JSONL-файла (``file_id``)."""

    def __init__(self, reader: ImportFileReader) -> None:
        self._reader = reader

    def validate(self, ref: str | None, file_id: str | None) -> None:
        """Нужен только ``file_id`` вида ``<имя>.jsonl``."""
        if ref is not None or not file_id or not FILE_ID_RE.match(file_id):
            raise _bad_ref("для source=file нужен file_id вида <имя>.jsonl")

    async def entries(self, job: ImportJob) -> AsyncIterator[ImportedItem | ValueError]:
        """Строки файла."""
        # Задание создаётся только после validate(): file_id у него есть.
        assert job.file_id is not None  # noqa: S101
        async for entry in self._reader.read(job.file_id):
            yield entry


class FilmEntrySource:
    """Импорт фильма по id источника (TMDB). Поиск по тексту не поддерживается."""

    def __init__(self, provider: FilmMetadataProvider) -> None:
        self._provider = provider

    def validate(self, ref: str | None, file_id: str | None) -> None:
        """Нужен ``ref`` — id фильма (``search:`` не поддерживается)."""
        ref = _require_ref(ref, file_id)
        if ref.startswith(SEARCH_PREFIX):
            raise _bad_ref("поиск по тексту для этого источника не поддерживается")
        _plain_ref(ref)

    async def entries(self, job: ImportJob) -> AsyncIterator[ImportedItem | ValueError]:
        """Один фильм."""
        yield await self._provider.fetch_film(_job_ref(job))


class MusicEntrySource:
    """Импорт треков: по id (``ref``) или поиском (``search:<текст>``)."""

    def __init__(self, provider: MusicMetadataProvider, search_limit: int) -> None:
        self._provider = provider
        self._search_limit = search_limit

    def validate(self, ref: str | None, file_id: str | None) -> None:
        """Нужен ``ref``: id трека или ``search:<текст>`` до 100 символов."""
        ref = _require_ref(ref, file_id)
        if ref.startswith(SEARCH_PREFIX):
            query = ref.removeprefix(SEARCH_PREFIX).strip()
            if not query or len(query) > MAX_SEARCH_LEN:
                raise _bad_ref("недопустимый поисковый ref")
            return
        _plain_ref(ref)

    async def entries(self, job: ImportJob) -> AsyncIterator[ImportedItem | ValueError]:
        """Найденные треки или один трек по id."""
        ref = _job_ref(job)
        if ref.startswith(SEARCH_PREFIX):
            query = ref.removeprefix(SEARCH_PREFIX).strip()
            for item in await self._provider.search_tracks(query, self._search_limit):
                yield item
            return
        yield await self._provider.get_track(ref)
