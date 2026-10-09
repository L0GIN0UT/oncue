"""Ключи объектов и политика выдачи ссылок (ADR 0012)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

_TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")
_EXT_RE = re.compile(r"^[a-z0-9]{1,8}$")
ASSET_PREFIX = "a/"
SOURCE_PREFIX = "s/"


def _object_key(prefix: str, token: str, ext: str) -> str:
    if not _TOKEN_RE.match(token):
        raise ValueError("токен ключа объекта — ровно 32 hex-символа (128 бит случайности)")
    if not _EXT_RE.match(ext):
        raise ValueError(f"недопустимое расширение: {ext!r}")
    return f"{prefix}{token}.{ext}"


def asset_object_key(token: str, ext: str) -> str:
    """Ключ ассета ``a/<32 hex>.<ext>``.

    В ключе нет ни id единицы, ни уровня: по URL одного уровня нельзя угадать другой,
    а ключ не раскрывает ответ (ADR 0012).
    """
    return _object_key(ASSET_PREFIX, token, ext)


def source_object_key(token: str, ext: str) -> str:
    """Ключ нормализованного исходника ``s/<32 hex>.<ext>`` (бакет исходников, не публичный)."""
    return _object_key(SOURCE_PREFIX, token, ext)


@dataclass(frozen=True, slots=True)
class LinkPolicy:
    """Правила выдачи подписанных ссылок.

    Attributes:
        ttl_s: Время жизни ссылки (D4: 60 с).
        max_per_window: Сколько ссылок на один уровень набора можно выдать одному субъекту
            за окно ``window_s`` (замена одноразовости, ADR 0012).
        window_s: Окно лимита, секунды.
    """

    ttl_s: int
    max_per_window: int
    window_s: int

    def __post_init__(self) -> None:
        if not 1 <= self.ttl_s <= 3600:  # noqa: PLR2004 — границы здравого смысла, не настройка
            raise ValueError("ttl_s должен быть от 1 до 3600 секунд")
        if self.max_per_window < 1 or self.window_s < 1:
            raise ValueError("max_per_window и window_s должны быть положительными")

    def expires_at(self, now: datetime) -> datetime:
        """Момент истечения ссылки, выданной в ``now`` (с точностью до секунды)."""
        return now.replace(microsecond=0) + timedelta(seconds=self.ttl_s)

    @staticmethod
    def subject_hash(subject: str) -> str:
        """sha256 hex субъекта: в журнале выдачи и в Redis идентификаторы не храним (ПДн)."""
        return hashlib.sha256(subject.encode()).hexdigest()

    @staticmethod
    def rate_key(subject: str, asset_set_id: UUID, level: int) -> str:
        """Ключ счётчика лимита. Субъект хэшируется: в Redis не должно быть идентификаторов."""
        return f"{LinkPolicy.subject_hash(subject)[:32]}:{asset_set_id.hex}:{level}"
