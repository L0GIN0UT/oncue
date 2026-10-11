"""Нормализация названий — реэкспорт из ``gv_text`` (план этапа 2, §2.3).

Реализация переехала в ``libs/text``, чтобы game_kernel сверял ответы той же функцией,
что строит алиасы каталога. Импорты catalog не меняются; регресс поведения ловит
собственная golden-таблица ``tests/unit/test_normalization.py``.
"""

from __future__ import annotations

from gv_text.normalization import DEFAULT_ARTICLES, NormalizerConfig, TitleNormalizer

__all__ = ["DEFAULT_ARTICLES", "NormalizerConfig", "TitleNormalizer"]
