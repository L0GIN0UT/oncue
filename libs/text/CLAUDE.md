# libs/text (`gv_text`)

Нормализация названий и алиасов (`TitleNormalizer`, `NormalizerConfig`, транслит ru↔lat — `TitleNormalizer.translit`). Чистый Python, только stdlib (import-linter `text-pure`). План — `docs/plans/stage-2.md` §2.3, §4.

## Особенности
- Перенесено из catalog без изменения поведения; `catalog_service.domain.normalization` — реэкспорт, golden-таблица catalog остаётся как регресс.
- Golden-таблица `tests/test_normalization.py` — контракт: по ней catalog строит алиасы, а game_kernel сверяет ответы. Новые правила (`&`/«и»/and, `damerau_levenshtein`) — только через неё (поток A).
