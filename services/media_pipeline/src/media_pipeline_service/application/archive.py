"""Безопасное чтение zip-пакета: проверки до распаковки и ограниченное чтение файлов.

Архив никогда не распаковывается на диск по именам из него: файл читается в память по
записи ``ZipInfo`` и сразу уходит в приём исходника, поэтому обход каталогов (``../``,
абсолютные пути, симлинки) невозможен по построению. Проверки ниже отвергают такие архивы ещё
и явно (защита в глубину) и ограничивают ресурсы: число записей, распакованный размер,
степень сжатия. Содержимое файлов не исполняется и не интерпретируется (кроме
``manifest.json`` - JSON).

Размеры в заголовках архива верим только как нижней оценке: чтение файла ограничено лимитом
независимо от заявленного ``file_size``.
"""

from __future__ import annotations

import stat
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from media_pipeline_service.domain.packages import PackageRejectedError

_ALLOWED_COMPRESSION = frozenset({zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})
_ZIP_MAGIC = b"PK\x03\x04"
_ENCRYPTED_FLAG = 0x1
_MAX_NAME_LEN = 300
_FIRST_PRINTABLE, _DEL = 32, 127


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    """Лимиты пакета (``GV_MEDIA__PACKAGE_*``).

    Attributes:
        max_archive_bytes: Размер самого архива.
        max_entries: Записей в архиве (файлы и каталоги).
        max_unpacked_bytes: Сумма заявленных распакованных размеров.
        max_entry_bytes: Размер одного файла.
        max_ratio: Предельная степень сжатия (распакованный / сжатый размер), по записи и
            по архиву в целом.
        max_manifest_bytes: Размер ``manifest.json``.
    """

    max_archive_bytes: int
    max_entries: int
    max_unpacked_bytes: int
    max_entry_bytes: int
    max_ratio: int
    max_manifest_bytes: int


def normalize_name(name: str) -> str:
    """Нормализовать имя записи: Unicode NFC (так одинаково сравниваются «й» и «и+◌̆»)."""
    return unicodedata.normalize("NFC", name)


def _check_name(name: str) -> None:
    """Имя записи безопасно: относительный путь без ``..``, ``\\``, диска и управляющих."""
    unsafe = (
        not name
        or len(name) > _MAX_NAME_LEN
        or name.startswith("/")
        or "\\" in name
        or (len(name) > 1 and name[1] == ":")
        or any(ord(c) < _FIRST_PRINTABLE or ord(c) == _DEL for c in name)
    )
    if unsafe:
        raise PackageRejectedError("небезопасное имя файла в архиве", code="package.unsafe_path")
    for part in name.rstrip("/").split("/"):
        if part in {"", ".", ".."}:
            raise PackageRejectedError(
                "небезопасное имя файла в архиве", code="package.unsafe_path"
            )


class SafeArchive:
    """Проверенный архив: имена, размеры и ограниченное чтение файлов.

    Создаётся через ``open_archive``; закрывать ``close()``. Методы синхронные и работают с
    диском - вызывать из потока (``asyncio.to_thread``).
    """

    def __init__(
        self, zf: zipfile.ZipFile, entries: dict[str, zipfile.ZipInfo], limits: ArchiveLimits
    ):
        self._zf = zf
        self._entries = entries
        self._limits = limits
        self._read_total = 0

    @property
    def names(self) -> frozenset[str]:
        """Нормализованные имена файлов (без каталогов)."""
        return frozenset(self._entries)

    def read(self, name: str, *, max_bytes: int) -> bytes:
        """Прочитать файл не больше ``max_bytes`` и не выходя за суммарный лимит.

        Raises:
            PackageRejectedError: нет такого файла, файл больше лимита, суммарно прочитано
                больше ``max_unpacked_bytes`` или данные повреждены.
        """
        info = self._entries.get(normalize_name(name))
        if info is None:
            raise PackageRejectedError("файла нет в архиве", code="package.file_missing")
        limit = min(max_bytes, self._limits.max_entry_bytes)
        try:
            with self._zf.open(info) as handle:
                data = handle.read(limit + 1)
        except (
            zipfile.BadZipFile,
            zipfile.LargeZipFile,
            zlib.error,
            EOFError,
            OSError,
            RuntimeError,
            NotImplementedError,
        ) as exc:
            raise PackageRejectedError(
                "файл в архиве повреждён", code="package.entry_corrupt"
            ) from exc
        if len(data) > limit:
            raise PackageRejectedError(
                "файл в архиве больше допустимого", code="package.entry_too_large"
            )
        self._read_total += len(data)
        if self._read_total > self._limits.max_unpacked_bytes:
            raise PackageRejectedError(
                "суммарный размер файлов больше лимита", code="package.unpacked_too_large"
            )
        return data

    def close(self) -> None:
        """Закрыть файл архива."""
        self._zf.close()


def open_archive(path: Path, limits: ArchiveLimits) -> SafeArchive:
    """Открыть архив и проверить его целиком до чтения содержимого.

    Raises:
        PackageRejectedError: не zip; размер, число записей, распакованный размер или
            степень сжатия выше лимитов; небезопасные или повторяющиеся имена; симлинки и
            спецфайлы; шифрование и неподдерживаемое сжатие.
    """
    size = path.stat().st_size
    if size > limits.max_archive_bytes:
        raise PackageRejectedError("архив больше лимита", code="package.archive_too_large")
    with path.open("rb") as raw:
        if raw.read(len(_ZIP_MAGIC)) != _ZIP_MAGIC:
            raise PackageRejectedError("это не zip-архив", code="package.not_zip")
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise PackageRejectedError("архив повреждён", code="package.not_zip") from exc
    try:
        infos = zf.infolist()
        entries = _check_entries(infos, limits, size)
    except BaseException:
        zf.close()
        raise
    return SafeArchive(zf, entries, limits)


def _check_entries(
    infos: list[zipfile.ZipInfo], limits: ArchiveLimits, archive_bytes: int
) -> dict[str, zipfile.ZipInfo]:
    if len(infos) > limits.max_entries:
        raise PackageRejectedError(
            "в архиве слишком много записей", code="package.too_many_entries"
        )
    entries: dict[str, zipfile.ZipInfo] = {}
    seen: set[str] = set()
    total = 0
    for info in infos:
        name = normalize_name(info.filename)
        # ``orig_filename`` - имя из архива как есть: zipfile сам правит ``filename`` (``\``, NUL).
        _check_name(normalize_name(info.orig_filename))
        folded = name.rstrip("/").casefold()
        if folded in seen:
            raise PackageRejectedError(
                "в архиве повторяются имена файлов", code="package.duplicate_name"
            )
        seen.add(folded)
        # Нет битов типа (zip из Python/Windows) - обычный файл; тип задан - только файл/каталог.
        file_type = stat.S_IFMT(info.external_attr >> 16)
        if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
            code = "package.symlink" if file_type == stat.S_IFLNK else "package.special_file"
            raise PackageRejectedError("в архиве ссылки или спецфайлы", code=code)
        if info.is_dir():
            continue
        if info.flag_bits & _ENCRYPTED_FLAG:
            raise PackageRejectedError("архив зашифрован", code="package.encrypted")
        if info.compress_type not in _ALLOWED_COMPRESSION:
            raise PackageRejectedError(
                "неподдерживаемое сжатие в архиве", code="package.compression_unsupported"
            )
        if info.file_size > limits.max_entry_bytes:
            raise PackageRejectedError(
                "файл в архиве больше допустимого", code="package.entry_too_large"
            )
        if info.file_size > max(info.compress_size, 1) * limits.max_ratio:
            raise PackageRejectedError(
                "слишком высокая степень сжатия файла", code="package.ratio_exceeded"
            )
        total += info.file_size
        if total > limits.max_unpacked_bytes:
            raise PackageRejectedError(
                "суммарный размер файлов больше лимита", code="package.unpacked_too_large"
            )
        entries[name] = info
    if total > max(archive_bytes, 1) * limits.max_ratio:
        raise PackageRejectedError(
            "слишком высокая степень сжатия архива", code="package.ratio_exceeded"
        )
    return entries
