"""Value objects identity: никнейм, e-mail, отображаемое имя, провайдер (ADR 0012, D9)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from email_validator import EmailNotValidError, validate_email

from gv_core.errors import InvalidInputError

_NICKNAME_RE = re.compile(r"^[0-9A-Za-zА-Яа-яЁё_.\-]{3,24}$")
_DISPLAY_MAX = 40
_EMAIL_MAX = 254


@dataclass(frozen=True, slots=True)
class Nickname:
    """Никнейм: 3–24 символа (латиница, кириллица, цифры, ``_ . -``), NFKC.

    Уникальность — без учёта регистра (``citext`` в БД). Значение хранится как ввёл
    пользователь, после NFKC и обрезки пробелов.
    """

    value: str

    @classmethod
    def parse(cls, raw: str) -> Nickname:
        """Нормализовать и проверить ввод пользователя.

        Raises:
            InvalidInputError: ``user.nickname_invalid``.
        """
        value = unicodedata.normalize("NFKC", raw).strip()
        if not _NICKNAME_RE.fullmatch(value):
            raise InvalidInputError(
                "никнейм: 3–24 символа — буквы, цифры, «_», «.», «-»",
                code="user.nickname_invalid",
            )
        return cls(value)

    @classmethod
    def tombstone(cls, user_hex: str) -> Nickname:
        """Ник удалённого аккаунта: содержит «~», поэтому не совпадёт ни с одним настоящим."""
        return cls(f"~deleted~{user_hex}")


@dataclass(frozen=True, slots=True)
class EmailAddress:
    """E-mail: нижний регистр, домен в IDNA (punycode). Только ASCII-локальная часть.

    ``repr`` не раскрывает адрес — объект может случайно попасть в лог.
    """

    value: str

    def __repr__(self) -> str:
        return "EmailAddress(***)"

    @classmethod
    def parse(cls, raw: str) -> EmailAddress:
        """Нормализовать и проверить синтаксис (без DNS-проверки доставки).

        Raises:
            InvalidInputError: ``user.email_invalid``.
        """
        candidate = raw.strip()
        if not candidate or len(candidate) > _EMAIL_MAX:
            raise InvalidInputError("некорректный e-mail", code="user.email_invalid")
        try:
            info = validate_email(candidate, check_deliverability=False, allow_smtputf8=False)
        except EmailNotValidError as exc:
            raise InvalidInputError("некорректный e-mail", code="user.email_invalid") from exc
        value = (info.ascii_email or info.normalized).lower()
        return cls(value)

    @classmethod
    def tombstone(cls, user_hex: str) -> EmailAddress:
        """Адрес удалённого аккаунта (домен ``.invalid`` зарезервирован RFC 2606)."""
        return cls(f"{user_hex}@deleted.invalid")


@dataclass(frozen=True, slots=True)
class DisplayName:
    """Отображаемое имя: 1–40 символов после NFKC, без управляющих символов."""

    value: str

    @classmethod
    def parse(cls, raw: str) -> DisplayName:
        """Нормализовать и проверить.

        Raises:
            InvalidInputError: ``user.display_name_invalid``.
        """
        value = unicodedata.normalize("NFKC", raw).strip()
        if (
            not value
            or len(value) > _DISPLAY_MAX
            or any(unicodedata.category(ch).startswith("C") for ch in value)
        ):
            raise InvalidInputError("имя: от 1 до 40 символов", code="user.display_name_invalid")
        return cls(value)


class Provider(StrEnum):
    """Внешние сервисы, которые можно привязать к аккаунту (контракт событий identity)."""

    YANDEX = "yandex"
    SPOTIFY = "spotify"
    SHIKIMORI = "shikimori"
    ANILIST = "anilist"
    TRAKT = "trakt"
