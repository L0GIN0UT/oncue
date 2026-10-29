"""Пакетная загрузка исходников архивом: пакет, ошибки по файлам и манифест.

Манифест ``manifest.json`` описан схемой ``contracts/packages/media_package_manifest.v1.json``;
разбор здесь повторяет её проверки (соответствие держит контрактный тест). Метаданных записей
каталога в нём нет: пакет несёт файлы к единицам, которые уже есть в каталоге.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from gv_core.domain import Entity
from gv_core.errors import InvalidInputError
from media_pipeline_service.domain.sources import SourceKind, SourceOrigin

PACKAGE_PREFIX = "k/"
MANIFEST_VERSION = 1
MANIFEST_NAME = "manifest.json"
MAX_FILES_PER_ITEM = 100
MAX_PATH_LEN = 300
MAX_KEY_LEN = 190
MAX_POSITION = 100
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_:\-.]*$")
# Роли манифеста = виды файловых исходников (``text`` приходит из catalog, файлом не грузится).
ROLE_KINDS: dict[str, SourceKind] = {
    "audio": SourceKind.AUDIO,
    "frame": SourceKind.FRAME,
    "poster": SourceKind.POSTER,
    "soundtrack": SourceKind.SOUNDTRACK,
    "audio_clip": SourceKind.AUDIO_CLIP,
    "cover": SourceKind.COVER,
}
ITEM_KINDS = frozenset({"track", "film"})
_ORIGINS = frozenset({SourceOrigin.UPLOAD, SourceOrigin.SEED, SourceOrigin.TMDB})


class PackageRejectedError(InvalidInputError):
    """Пакет отвергнут целиком: плохой архив или манифест (в статусе пакета - ``failed``)."""

    default_code = "package.invalid"


class PackageStatus(StrEnum):
    """Состояние пакета."""

    QUEUED = "queued"
    PROCESSING = "processing"
    DONE = "done"  # все файлы приняты
    PARTIAL = "partial"  # часть файлов с ошибками
    FAILED = "failed"  # пакет не разобран или ни один файл не принят

    @property
    def is_terminal(self) -> bool:
        """Обработка закончена."""
        return self not in {PackageStatus.QUEUED, PackageStatus.PROCESSING}


@dataclass(frozen=True, slots=True)
class PackageFileError:
    """Ошибка одного файла (или записи) пакета.

    Attributes:
        seq: Номер ошибки в пакете (с 1).
        item_key: Ключ записи манифеста.
        path: Путь файла в архиве (нормализованный).
        role: Роль файла.
        code: Машинный код (``source.*``, ``package.*``).
        message: Короткое пояснение без внутренних деталей (до 500 символов).
    """

    seq: int
    item_key: str | None
    path: str | None
    role: str | None
    code: str
    message: str


class ImportPackage(Entity[UUID]):
    """Пакет: архив в S3, счётчики и ошибки по файлам.

    Счётчики считаются по файлам. ``errors`` хранит не больше ``keep_errors`` ошибок, остальные
    учитываются в ``errors_dropped`` (список в статусе не растёт без предела).
    """

    def __init__(
        self,
        id: UUID,  # noqa: A002
        *,
        actor_id: UUID,
        reason: str,
        archive_key: str | None,
        archive_sha256: str,
        archive_bytes: int,
        created_at: datetime,
        status: PackageStatus = PackageStatus.QUEUED,
        total: int = 0,
        done: int = 0,
        failed: int = 0,
        missing_source: int = 0,
        missing_license: int = 0,
        errors_dropped: int = 0,
        error_code: str | None = None,
        error_message: str | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        heartbeat_at: datetime | None = None,
        errors: list[PackageFileError] | None = None,
    ) -> None:
        super().__init__(id)
        self.actor_id = actor_id
        self.reason = reason
        self.archive_key = archive_key
        self.archive_sha256 = archive_sha256
        self.archive_bytes = archive_bytes
        self.created_at = created_at
        self.status = status
        self.total = total
        self.done = done
        self.failed = failed
        self.missing_source = missing_source
        self.missing_license = missing_license
        self.errors_dropped = errors_dropped
        self.error_code = error_code
        self.error_message = error_message
        self.started_at = started_at
        self.finished_at = finished_at
        self.heartbeat_at = heartbeat_at
        self.errors: list[PackageFileError] = errors or []
        # Ошибки, ещё не записанные в БД, и признак «старые ошибки стереть» (перезапуск).
        self.new_errors: list[PackageFileError] = []
        self.errors_reset = False

    def can_claim(self, now: datetime, lease: timedelta) -> bool:
        """Воркер может взять пакет: он в очереди или прежний воркер замолчал дольше ``lease``.

        Живой воркер обновляет ``heartbeat_at`` после каждого файла, поэтому повторная доставка
        задачи (ack-timeout брокера) не запускает вторую обработку того же пакета.
        """
        if self.status is PackageStatus.QUEUED:
            return True
        if self.status is not PackageStatus.PROCESSING:
            return False
        return self.heartbeat_at is None or now - self.heartbeat_at > lease

    def touch(self, now: datetime) -> None:
        """Отметить, что воркер жив."""
        self.heartbeat_at = now

    def start(self, now: datetime) -> None:
        """Начать (или начать заново после падения воркера) обработку: счётчики обнуляются.

        Повторный проход безопасен: исходники грузятся с детерминированными ключами
        идемпотентности и повторно не создаются.
        """
        self.status = PackageStatus.PROCESSING
        self.started_at = now
        self.heartbeat_at = now
        self.finished_at = None
        self.total = self.done = self.failed = 0
        self.missing_source = self.missing_license = self.errors_dropped = 0
        self.error_code = self.error_message = None
        self.errors, self.new_errors, self.errors_reset = [], [], True

    def set_total(self, total: int) -> None:
        """Сколько файлов в манифесте."""
        self.total = total

    def record_done(self, *, source_missing: bool, license_missing: bool) -> None:
        """Файл принят."""
        self.done += 1
        self.missing_source += int(source_missing)
        self.missing_license += int(license_missing)

    def record_failed(
        self,
        *,
        item_key: str | None,
        path: str | None,
        role: str | None,
        code: str,
        message: str,
        keep_errors: int,
    ) -> None:
        """Файл не принят: счётчик и (в пределах лимита) строка ошибки."""
        self.failed += 1
        if len(self.errors) >= keep_errors:
            self.errors_dropped += 1
            return
        error = PackageFileError(
            seq=len(self.errors) + 1,
            item_key=item_key,
            path=path,
            role=role,
            code=code[:64],
            message=message[:500],
        )
        self.errors.append(error)
        self.new_errors.append(error)

    def finish(self, now: datetime) -> None:
        """Обработка закончена: итоговый статус по счётчикам."""
        if self.failed == 0:
            self.status = PackageStatus.DONE
        elif self.done > 0:
            self.status = PackageStatus.PARTIAL
        else:
            self.status = PackageStatus.FAILED
        self.finished_at = now
        self.archive_key = None

    def fail(self, now: datetime, *, code: str, message: str) -> None:
        """Пакет отвергнут целиком (архив или манифест)."""
        self.status = PackageStatus.FAILED
        self.error_code = code[:64]
        self.error_message = message[:500]
        self.finished_at = now
        self.archive_key = None


@dataclass(frozen=True, slots=True)
class ManifestFile:
    """Файл записи: роль, путь в архиве и порядок среди исходников вида."""

    role: str
    path: str
    position: int | None = None

    @property
    def kind(self) -> SourceKind:
        """Вид исходника для роли."""
        return ROLE_KINDS[self.role]


@dataclass(frozen=True, slots=True)
class ManifestItem:
    """Запись манифеста: единица каталога и её файлы."""

    key: str
    kind: str
    files: tuple[ManifestFile, ...]
    media_item_id: UUID | None = None
    origin: SourceOrigin | None = None
    source_url: str | None = None
    license_note: str | None = None


@dataclass(frozen=True, slots=True)
class PackageManifest:
    """Разобранный ``manifest.json``."""

    items: tuple[ManifestItem, ...] = field(default_factory=tuple)

    @property
    def file_count(self) -> int:
        """Сколько файлов во всех записях."""
        return sum(len(i.files) for i in self.items)


def package_object_key(token: str) -> str:
    """Ключ архива пакета ``k/<hex>.zip`` (бакет исходников, не публичный; ключ из ``secrets``)."""
    return f"{PACKAGE_PREFIX}{token}.zip"


def _bad(message: str, code: str = "package.manifest_invalid") -> PackageRejectedError:
    return PackageRejectedError(message, code=code)


def _optional_str(raw: Any, name: str, max_len: int) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or len(raw) > max_len:
        raise _bad(f"поле {name}: ожидается строка до {max_len} символов")
    return raw or None


def parse_manifest(raw: bytes, *, max_files: int) -> PackageManifest:
    """Разобрать и проверить ``manifest.json``.

    Пустые ``source_url`` и ``license_note`` равнозначны отсутствию (ADR 0072). Лишние поля
    отвергаются (``additionalProperties: false`` схемы). Пути здесь только проверяются на
    форму; безопасность имён в архиве проверяет инспектор архива.

    Raises:
        PackageRejectedError: не JSON, не по схеме или файлов больше ``max_files``.
    """
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise _bad("manifest.json не является корректным JSON") from exc
    if not isinstance(data, dict) or set(data) != {"version", "items"}:
        raise _bad("manifest.json: ожидаются ровно поля version и items")
    if data["version"] != MANIFEST_VERSION or isinstance(data["version"], bool):
        raise _bad(f"manifest.json: поддерживается version {MANIFEST_VERSION}")
    raw_items = data["items"]
    if not isinstance(raw_items, list) or not raw_items:
        raise _bad("manifest.json: items должен быть непустым списком")
    items: list[ManifestItem] = []
    total = 0
    for n, raw_item in enumerate(raw_items, start=1):
        item = _parse_item(raw_item, n)
        total += len(item.files)
        if total > max_files:
            raise _bad(f"в манифесте больше {max_files} файлов", code="package.too_many_files")
        items.append(item)
    return PackageManifest(tuple(items))


_ITEM_FIELDS = {"key", "kind", "files", "media_item_id", "origin", "source_url", "license_note"}


def _parse_item(raw: Any, n: int) -> ManifestItem:
    where = f"items[{n}]"
    if not isinstance(raw, dict) or not {"key", "kind", "files"} <= set(raw) <= _ITEM_FIELDS:
        raise _bad(f"{where}: нужны key, kind, files и только допустимые поля")
    key = raw["key"]
    if not isinstance(key, str) or len(key) > MAX_KEY_LEN or not _KEY_RE.fullmatch(key):
        raise _bad(f"{where}: некорректный key")
    if not isinstance(raw["kind"], str) or raw["kind"] not in ITEM_KINDS:
        raise _bad(f"{where}: kind должен быть track или film")
    files_raw = raw["files"]
    if not isinstance(files_raw, list) or not 1 <= len(files_raw) <= MAX_FILES_PER_ITEM:
        raise _bad(f"{where}: files — список от 1 до {MAX_FILES_PER_ITEM} файлов")
    media_item_id: UUID | None = None
    if raw.get("media_item_id") is not None:
        try:
            media_item_id = UUID(str(raw["media_item_id"]))
        except ValueError as exc:
            raise _bad(f"{where}: media_item_id не UUID") from exc
    origin: SourceOrigin | None = None
    if raw.get("origin") is not None:
        try:
            origin = SourceOrigin(raw["origin"])
        except ValueError as exc:
            raise _bad(f"{where}: неизвестный origin") from exc
        if origin not in _ORIGINS:
            raise _bad(f"{where}: origin вне upload|seed|tmdb")
    return ManifestItem(
        key=key,
        kind=raw["kind"],
        files=tuple(_parse_file(f, where, i) for i, f in enumerate(files_raw, start=1)),
        media_item_id=media_item_id,
        origin=origin,
        source_url=_optional_str(raw.get("source_url"), f"{where}.source_url", 2000),
        license_note=_optional_str(raw.get("license_note"), f"{where}.license_note", 500),
    )


def _parse_file(raw: Any, where: str, n: int) -> ManifestFile:
    at = f"{where}.files[{n}]"
    if not isinstance(raw, dict) or not {"role", "path"} <= set(raw) <= {
        "role",
        "path",
        "position",
    }:
        raise _bad(f"{at}: нужны role и path, допустим position")
    role, path = raw["role"], raw["path"]
    if not isinstance(role, str) or role not in ROLE_KINDS:
        raise _bad(f"{at}: неизвестная role", code="package.role_unknown")
    if not isinstance(path, str) or not 1 <= len(path) <= MAX_PATH_LEN:
        raise _bad(f"{at}: path — строка от 1 до {MAX_PATH_LEN} символов")
    position = raw.get("position")
    if position is not None and (
        isinstance(position, bool)
        or not isinstance(position, int)
        or not 1 <= position <= MAX_POSITION
    ):
        raise _bad(f"{at}: position — число от 1 до 100")
    return ManifestFile(role=role, path=path, position=position)
