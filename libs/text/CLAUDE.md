# libs/text (`gv_text`)

Нормализация названий и алиасов (`TitleNormalizer`, `NormalizerConfig`, транслит ru↔lat — `TitleNormalizer.translit`), замены союзов (`CONJUNCTION_REPLACEMENTS`), расстояние `damerau_levenshtein` (полная версия, метрика, ранний выход по `max_distance`). Чистый Python, только stdlib (import-linter `text-pure`). План — `docs/plans/stage-2.md` §2.3, §4.

## Особенности
- Перенесено из catalog без изменения поведения; `catalog_service.domain.normalization` — реэкспорт, golden-таблица catalog остаётся как регресс.
- Golden-таблица `tests/test_normalization.py` — контракт: по ней catalog строит алиасы, а game_kernel сверяет ответы. Новые правила — только через неё.
- Замены по умолчанию выключены (`NormalizerConfig().replacements == ()`): catalog строит алиасы как раньше, сверка ответов включает `CONJUNCTION_REPLACEMENTS`. Совместимость сторон: `M(N(x)) == M(x)` (property-тест).
