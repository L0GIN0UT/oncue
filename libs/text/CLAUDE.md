# libs/text (`gv_text`)

Нормализация названий и алиасов (`TitleNormalizer`, `NormalizerConfig`, транслит ru↔lat — `TitleNormalizer.translit`), замены союзов (`CONJUNCTION_REPLACEMENTS`), расстояние `damerau_levenshtein` (полная версия, метрика, ранний выход по `max_distance`). Этап 3b: `ugc` (`normalize_ugc`, `UgcPolicy`, `UgcViolation` с кодами `ugc.*`, ADR 0030) и `emoji` (`graphemes`, `is_strict_emoji`, таблица RGI). Чистый Python, только stdlib (import-linter `text-pure`). Планы — `docs/plans/stage-2.md` §2.3, §4, `docs/plans/stage-3b.md` §2.7.

## Особенности
- Перенесено из catalog без изменения поведения; `catalog_service.domain.normalization` — реэкспорт, golden-таблица catalog остаётся как регресс.
- Golden-таблица `tests/test_normalization.py` — контракт: по ней catalog строит алиасы, а game_kernel сверяет ответы. Новые правила — только через неё.
- Замены по умолчанию выключены (`NormalizerConfig().replacements == ()`): catalog строит алиасы как раньше, сверка ответов включает `CONJUNCTION_REPLACEMENTS`. Совместимость сторон: `M(N(x)) == M(x)` (property-тест).
- UGC: нормализация (NFKC, удаление Cc/Cf/Cs/Co, ZWJ — только внутри эмодзи) **до** длины и denylist; длина — кодовые точки. Denylist сверяется по `skeleton` (похожие символы → кириллица, диакритика, повторы, знаки внутри слова); записи `слово` / `основа*` / `*часть*`, встроенный список — `data/ugc_denylist.txt`, свой — `GV_UGC__DENYLIST_PATH` (заменяет целиком). Слова через пробел («х у й») и «[точка]»-ссылки не ловятся — модерация постфактум.
- Эмодзи: `data/emoji-sequences.txt` — RGI Unicode Emoji 16.0 (3790, лицензия `data/LICENSE-UNICODE.txt`), не `unicodedata` (в Python 3.13 — Unicode 15.1). `graphemes` совпадает с catalog и media-pipeline (общие golden-векторы).
