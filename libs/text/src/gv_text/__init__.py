"""gv_text — нормализация названий и алиасов (чистый Python, без зависимостей).

Общая для catalog (индекс и автокомплит) и game_kernel (сверка ответов): одна и та же
нормализация с обеих сторон — иначе ответ «Ёжик в тумане» не совпадёт с алиасом каталога.
Контракт поведения — golden-таблица ``libs/text/tests/test_normalization.py``.
"""

from gv_text.distance import damerau_levenshtein
from gv_text.normalization import (
    CONJUNCTION_REPLACEMENTS,
    DEFAULT_ARTICLES,
    NormalizerConfig,
    TitleNormalizer,
)

__all__ = [
    "CONJUNCTION_REPLACEMENTS",
    "DEFAULT_ARTICLES",
    "NormalizerConfig",
    "TitleNormalizer",
    "damerau_levenshtein",
]
