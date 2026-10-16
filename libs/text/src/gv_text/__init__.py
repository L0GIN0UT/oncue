"""gv_text — нормализация названий и алиасов, UGC-текст и эмодзи (чистый Python).

Общая для catalog (индекс и автокомплит) и game_kernel (сверка ответов): одна и та же
нормализация с обеих сторон — иначе ответ «Ёжик в тумане» не совпадёт с алиасом каталога.
Контракт поведения — golden-таблица ``libs/text/tests/test_normalization.py``.

Этап 3b (ADR 0030): ``gv_text.ugc`` — нормализация и проверка пользовательского текста
(сборники, полки), ``gv_text.emoji`` — графемы и строгая проверка RGI-эмодзи.
"""

from gv_text.distance import damerau_levenshtein
from gv_text.emoji import graphemes, is_strict_emoji
from gv_text.normalization import (
    CONJUNCTION_REPLACEMENTS,
    DEFAULT_ARTICLES,
    NormalizerConfig,
    TitleNormalizer,
)
from gv_text.ugc import UgcPolicy, UgcViolation, normalize_ugc

__all__ = [
    "CONJUNCTION_REPLACEMENTS",
    "DEFAULT_ARTICLES",
    "NormalizerConfig",
    "TitleNormalizer",
    "UgcPolicy",
    "UgcViolation",
    "damerau_levenshtein",
    "graphemes",
    "is_strict_emoji",
    "normalize_ugc",
]
