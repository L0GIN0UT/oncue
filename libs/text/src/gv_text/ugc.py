"""Минимальная безопасность пользовательского текста до модерации этапа 5 (ADR 0030, D8).

Порядок важен: сначала ``normalize_ugc`` (NFKC, удаление управляющих, bidi- и
zero-width-символов, схлопывание пробелов), и только потом проверка длины, denylist и
запретов — иначе ``"ху\\u200bй"`` или полноширинное ``"＠"`` проходили бы мимо проверок.

* длина считается в кодовых точках **после** нормализации (как ``[...s].length`` на
  фронтенде);
* denylist сверяется по «скелету» слова: регистр, похожие символы (латиница/греческий →
  кириллица), диакритика, повторы букв, точки и дефисы внутри слова не спасают;
* в названии (``forbid_links=True``) нельзя ссылок, доменов, ``@``-упоминаний и e-mail.

Ошибки — ``UgcViolation`` с машинным кодом ``ugc.*``: сервис переводит её в свою
доменную ошибку (``collections.title_invalid`` и т. п.), сохраняя ``code`` в ``details``.
Модуль — чистый Python (только stdlib), как весь ``gv_text``.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from importlib.resources import files
from pathlib import Path
from typing import Final

UGC_EMPTY: Final = "ugc.empty"
UGC_TOO_LONG: Final = "ugc.too_long"
UGC_DENIED: Final = "ugc.denied_word"
UGC_LINK: Final = "ugc.link_forbidden"
UGC_MENTION: Final = "ugc.mention_forbidden"
UGC_EMAIL: Final = "ugc.email_forbidden"

DEFAULT_DENYLIST_FILE: Final = "ugc_denylist.txt"
"""Встроенный denylist в каталоге ``data`` пакета (стартовый, ru/en)."""


class UgcViolation(ValueError):  # noqa: N818 — нарушение правила, не ошибка программы
    """Текст не прошёл проверку UGC.

    Attributes:
        code: Машинный код (``ugc.empty``, ``ugc.too_long``, ``ugc.denied_word``,
            ``ugc.link_forbidden``, ``ugc.mention_forbidden``, ``ugc.email_forbidden``).
        details: Подробности без исходного текста (``max_len``, ``length``): сам текст
            и найденное слово в ответ и логи не попадают.
    """

    def __init__(self, code: str, message: str, details: Mapping[str, int] | None = None):
        super().__init__(message)
        self.code = code
        self.details: dict[str, int] = dict(details or {})


# ----------------------------------------------------------------------------- нормализация
_ZWJ = "\u200d"
_BLACK_FLAG = "\U0001f3f4"
_TAGS = range(0xE0020, 0xE0080)
_SKIN_TONES = range(0x1F3FB, 0x1F400)
_VS16 = "\ufe0f"
# Неназначенные в Unicode 15.1 кодовые точки от U+1F000 — кандидаты в эмодзи 16.0+.
_NEW_EMOJI_PLANE_START = 0x1F000
# Управляющие (Cc), форматные (Cf: bidi-метки и изоляты, zero-width, мягкий перенос, BOM),
# суррогаты и частное использование. Неназначенные (Cn) не трогаем: в unicodedata Python
# 3.13 база Unicode 15.1, и эмодзи 16.0 для неё «неназначены».
_DROP_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co"})
_LINE_BREAKS = frozenset({"\n", "\r", "\u2028", "\u2029", "\x85", "\x0b", "\x0c"})


def _is_pictographic(ch: str) -> bool:
    """Грубая замена ``Extended_Pictographic``: символ-эмодзи или его модификатор."""
    if ch == _VS16 or ord(ch) in _SKIN_TONES:
        return True
    category = unicodedata.category(ch)
    return category == "So" or (category == "Cn" and ord(ch) >= _NEW_EMOJI_PLANE_START)


def _keep_format(text: str, i: int, out: list[str]) -> bool:
    """Оставить форматный символ ``text[i]``: ZWJ внутри эмодзи и теги флага-тега."""
    ch = text[i]
    if ch == _ZWJ:
        prev_ok = bool(out) and _is_pictographic(out[-1])
        next_ok = i + 1 < len(text) and _is_pictographic(text[i + 1])
        return prev_ok and next_ok
    if ord(ch) in _TAGS:
        return bool(out) and (out[-1] == _BLACK_FLAG or ord(out[-1]) in _TAGS)
    return False


def normalize_ugc(text: str, *, multiline: bool = False) -> str:
    """Нормализовать пользовательский текст (без проверок длины и содержания).

    1. NFKC (полноширинные и «математические» буквы, лигатуры → обычные);
    2. удаление управляющих, bidi- и zero-width-символов (ZWJ остаётся только внутри
       эмодзи-последовательностей, теги — только во флаге-теге);
    3. пробельные символы → один пробел, края обрезаются; при ``multiline`` переводы
       строк сохраняются, но не больше одной пустой строки подряд.
    """
    text = unicodedata.normalize("NFKC", text).replace("\r\n", "\n")
    out: list[str] = []
    for i, ch in enumerate(text):
        if ch in _LINE_BREAKS:
            out.append("\n" if multiline else " ")
        elif ch == "\t" or ch.isspace():
            out.append(" ")
        elif unicodedata.category(ch) in _DROP_CATEGORIES and not _keep_format(text, i, out):
            continue
        else:
            out.append(ch)
    joined = "".join(out)
    if not multiline:
        return " ".join(joined.split())
    lines = [" ".join(line.split()) for line in joined.split("\n")]
    collapsed = re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    return collapsed.strip()


# ----------------------------------------------------------------------------- скелет
# Похожие символы → кириллица (после NFKC, до приведения регистра: «H» похожа на «Н»,
# а «h» — нет). Скелет — только для сравнения с denylist, наружу не выходит.
_CONFUSABLES: Final[Mapping[str, str]] = {
    # латиница
    "A": "а", "a": "а", "B": "в", "C": "с", "c": "с", "E": "е", "e": "е", "H": "н",
    "K": "к", "k": "к", "M": "м", "O": "о", "o": "о", "P": "р", "p": "р", "T": "т",
    "X": "х", "x": "х", "Y": "у", "y": "у", "u": "и", "r": "г", "n": "п",
    # греческий
    "Α": "а", "α": "а", "Β": "в", "Ε": "е", "ε": "е", "Η": "н", "Κ": "к", "κ": "к",
    "Μ": "м", "Ο": "о", "ο": "о", "Ρ": "р", "ρ": "р", "Τ": "т", "τ": "т", "Χ": "х",
    "χ": "х", "Υ": "у", "γ": "у", "η": "п", "π": "п",
    # цифры и знаки вместо букв
    "0": "о", "3": "з", "6": "б", "@": "а", "$": "s",
    # кириллица: ё → е, украинские/белорусские двойники
    "ё": "е", "Ё": "е", "і": "i", "І": "i", "ї": "i", "є": "е",
}  # fmt: skip
_JOINERS = re.compile(r"[\W_]+")


def skeleton(word: str) -> str:
    """«Скелет» слова для сравнения с denylist.

    Похожие символы → кириллица, регистр снят, диакритика снята, знаки внутри слова
    (``х.у.й``, ``f-u-c-k``) выброшены, повторы одной буквы схлопнуты (``ооо`` → ``о``).
    """
    # Диакритика снимается до замены похожих: «ü» → «u» → «и», «й» → «и», «ё» → «е».
    stripped = "".join(
        ch for ch in unicodedata.normalize("NFKD", word) if not unicodedata.combining(ch)
    )
    mapped = "".join(_CONFUSABLES.get(ch, ch) for ch in stripped).casefold()
    mapped = "".join(_CONFUSABLES.get(ch, ch) for ch in mapped)
    letters = _JOINERS.sub("", mapped)
    return re.sub(r"(.)\1+", r"\1", letters)


# ----------------------------------------------------------------------------- ссылки
_TLDS: Final = (
    "ru", "su", "рф", "com", "net", "org", "info", "biz", "io", "me", "co", "cc", "tv",
    "to", "ly", "gg", "app", "dev", "xyz", "top", "site", "online", "store", "shop",
    "link", "click", "club", "pro", "live", "fun", "space", "uk", "de", "ua", "by", "kz",
    "eu", "us", "tk", "ml", "ga", "cf", "gq",
)  # fmt: skip
_URL = re.compile(r"(?:[a-z][a-z0-9+.-]*://|www\.)", re.IGNORECASE)
_DOMAIN = re.compile(
    rf"(?<![\w.-])[\w-]+(?:\.[\w-]+)*\.(?:{'|'.join(_TLDS)})(?![\w-])", re.IGNORECASE
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.IGNORECASE)


# ----------------------------------------------------------------------------- политика
@dataclass(frozen=True, slots=True)
class UgcPolicy:
    """Проверка пользовательского текста: нормализация, длина, denylist, ссылки.

    Записи denylist (по одной в строке файла, ``#`` — комментарий):

    * ``слово`` — скелет слова текста равен скелету записи;
    * ``основа*`` — скелет слова начинается со скелета основы (словоформы);
    * ``*часть*`` — скелет слова содержит часть (осторожно: ложные срабатывания).

    Attributes:
        words: Скелеты целых слов.
        stems: Скелеты основ.
        infixes: Скелеты частей слова.
    """

    words: frozenset[str] = field(default_factory=frozenset)
    stems: tuple[str, ...] = ()
    infixes: tuple[str, ...] = ()

    @classmethod
    def from_entries(cls, entries: Iterable[str]) -> UgcPolicy:
        """Политика из записей denylist (формат — в описании класса)."""
        words: set[str] = set()
        stems: set[str] = set()
        infixes: set[str] = set()
        for raw in entries:
            entry = raw.split("#", 1)[0].strip()
            if not entry:
                continue
            core = skeleton(entry.strip("*"))
            if not core:
                continue
            if entry.startswith("*") and entry.endswith("*") and len(entry) > 1:
                infixes.add(core)
            elif entry.endswith("*"):
                stems.add(core)
            else:
                words.add(core)
        return cls(frozenset(words), tuple(sorted(stems)), tuple(sorted(infixes)))

    @classmethod
    def from_file(cls, path: str | Path | None = None) -> UgcPolicy:
        """Политика из файла denylist (``None`` — встроенный ``data/ugc_denylist.txt``)."""
        if path is None:
            text = files("gv_text").joinpath("data", DEFAULT_DENYLIST_FILE).read_text("utf-8")
        else:
            text = Path(path).read_text(encoding="utf-8")
        return cls.from_entries(text.splitlines())

    def denied(self, text: str) -> bool:
        """Есть ли в (нормализованном) тексте слово из denylist."""
        for token in text.split():
            word = skeleton(token)
            if not word:
                continue
            if word in self.words:
                return True
            if any(word.startswith(stem) for stem in self.stems):
                return True
            if any(part in word for part in self.infixes):
                return True
        return False

    def check(
        self,
        text: str,
        *,
        max_len: int,
        multiline: bool = False,
        forbid_links: bool = False,
    ) -> str:
        """Нормализовать и проверить текст; вернуть нормализованный.

        Args:
            text: Ввод пользователя.
            max_len: Максимум кодовых точек после нормализации.
            multiline: Сохранять переводы строк (описание).
            forbid_links: Запретить ссылки, домены, ``@`` и e-mail (название, тег, полка).

        Raises:
            UgcViolation: пусто, длиннее ``max_len``, слово из denylist, ссылка, e-mail
                или упоминание (коды ``ugc.*``).
        """
        cleaned = normalize_ugc(text, multiline=multiline)
        if not cleaned:
            raise UgcViolation(UGC_EMPTY, "пустой текст после нормализации")
        if len(cleaned) > max_len:
            raise UgcViolation(
                UGC_TOO_LONG,
                f"длиннее {max_len} символов",
                {"max_len": max_len, "length": len(cleaned)},
            )
        if forbid_links:
            if _EMAIL.search(cleaned):
                raise UgcViolation(UGC_EMAIL, "e-mail в тексте запрещён")
            if "@" in cleaned:
                raise UgcViolation(UGC_MENTION, "упоминания через @ запрещены")
            if _URL.search(cleaned) or _DOMAIN.search(cleaned):
                raise UgcViolation(UGC_LINK, "ссылки в тексте запрещены")
        if self.denied(cleaned):
            raise UgcViolation(UGC_DENIED, "недопустимое слово")
        return cleaned
