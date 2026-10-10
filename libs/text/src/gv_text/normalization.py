"""Нормализация названий для поиска, автокомплита и сверки ответов.

Одна функция используется и при записи алиасов, и при разборе запроса пользователя:
так «Ёжик в тумане», «ежик в тумане» и «ЁЖИК, в тумане!» дают одну и ту же строку.

Шаги ``TitleNormalizer.normalize``:
    1. NFKC (лигатуры, полноширинные символы, «½» → «1⁄2» и т. п.);
    2. casefold (регистронезависимость с учётом «ß» → «ss»);
    3. «ё» → «е»;
    4. снятие диакритики у латиницы («é» → «e»); кириллица не трогается, иначе «й» → «и»;
    5. пунктуация и символы → пробел, схлопывание пробелов;
    6. снятие ведущих артиклей из конфига («the», «a», «an»), если после них что-то есть.

Результат идемпотентен: ``normalize(normalize(x)) == normalize(x)`` (проверяется
property-based тестом).

Транслитерация (``translit``) — отдельная операция: из нормализованной строки на одном
алфавите строит вариант на другом (ru → lat и lat → ru). Её результат сохраняется как
алиас вида ``translit``, чтобы «interstellar» находил «Интерстеллар» и наоборот.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

DEFAULT_ARTICLES: tuple[str, ...] = ("the", "a", "an")

_RU_TO_LAT: dict[str, str] = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}

# Обратное направление неоднозначно, поэтому таблица своя: сначала длинные сочетания.
_LAT_TO_RU: tuple[tuple[str, str], ...] = (
    ("shch", "щ"),
    ("sch", "щ"),
    ("zh", "ж"),
    ("kh", "х"),
    ("ts", "ц"),
    ("ch", "ч"),
    ("sh", "ш"),
    ("yu", "ю"),
    ("ya", "я"),
    ("yo", "е"),
    ("ph", "ф"),
    ("th", "т"),
    ("ee", "и"),
    ("oo", "у"),
    ("a", "а"),
    ("b", "б"),
    ("c", "к"),
    ("d", "д"),
    ("e", "е"),
    ("f", "ф"),
    ("g", "г"),
    ("h", "х"),
    ("i", "и"),
    ("j", "дж"),
    ("k", "к"),
    ("l", "л"),
    ("m", "м"),
    ("n", "н"),
    ("o", "о"),
    ("p", "п"),
    ("q", "к"),
    ("r", "р"),
    ("s", "с"),
    ("t", "т"),
    ("u", "у"),
    ("v", "в"),
    ("w", "в"),
    ("x", "кс"),
    ("y", "и"),
    ("z", "з"),
)


def _is_latin_letter(ch: str) -> bool:
    return "a" <= ch <= "z"


def _is_cyrillic_letter(ch: str) -> bool:
    return "а" <= ch <= "я" or ch == "ё"


def _strip_latin_diacritics(text: str) -> str:
    """Убрать диакритику только у латинских букв («é» → «e», но «й» остаётся «й»)."""
    out: list[str] = []
    for ch in text:
        decomposed = unicodedata.normalize("NFD", ch)
        base = decomposed[0]
        if len(decomposed) > 1 and _is_latin_letter(base):
            out.append(base)
        else:
            out.append(ch)
    return "".join(out)


def _is_separator(ch: str) -> bool:
    """Пунктуация, символы и любые пробельные/управляющие символы."""
    category = unicodedata.category(ch)
    return category[0] in {"P", "S", "Z", "C"} or ch.isspace()


@dataclass(frozen=True, slots=True)
class NormalizerConfig:
    """Параметры нормализации (из настроек сервиса, не хардкод).

    Attributes:
        articles: Ведущие слова, которые отбрасываются («the beatles» → «beatles»).
    """

    articles: tuple[str, ...] = DEFAULT_ARTICLES


class TitleNormalizer:
    """Нормализатор названий и алиасов (см. описание модуля)."""

    def __init__(self, config: NormalizerConfig | None = None) -> None:
        self._articles = frozenset((config or NormalizerConfig()).articles)

    def normalize(self, text: str) -> str:
        """Привести строку к канонической форме для сравнения и поиска.

        Returns:
            Нормализованная строка; пустая, если в тексте нет букв и цифр.
        """
        value = unicodedata.normalize("NFKC", text).casefold()
        # casefold может вернуть не-NFC последовательность — нормализуем повторно.
        value = unicodedata.normalize("NFKC", value).replace("ё", "е")
        value = _strip_latin_diacritics(value)
        cleaned = "".join(" " if _is_separator(ch) else ch for ch in value)
        tokens = cleaned.split()
        while len(tokens) > 1 and tokens[0] in self._articles:
            tokens = tokens[1:]
        return " ".join(tokens)

    def translit(self, normalized: str) -> str | None:
        """Вариант нормализованной строки на другом алфавите.

        Кириллица → латиница и латиница → кириллица. Смешанные строки и строки без букв
        не транслитерируются (``None``): у них нет однозначного «другого» варианта.
        Результат снова нормализуется (снятие артиклей и т. п.).
        """
        has_cyr = any(_is_cyrillic_letter(ch) for ch in normalized)
        has_lat = any(_is_latin_letter(ch) for ch in normalized)
        if has_cyr == has_lat:
            return None
        converted = _ru_to_lat(normalized) if has_cyr else _lat_to_ru(normalized)
        result = self.normalize(converted)
        return result if result and result != normalized else None


def _ru_to_lat(text: str) -> str:
    return "".join(_RU_TO_LAT.get(ch, ch) for ch in text)


def _lat_to_ru(text: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        for src, dst in _LAT_TO_RU:
            if text.startswith(src, i):
                out.append(dst)
                i += len(src)
                break
        else:
            out.append(text[i])
            i += 1
    return "".join(out)
