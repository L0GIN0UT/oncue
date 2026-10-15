# Сиды каталога

Файлы импорта для источника `file` (`POST /admin/v1/imports {"source": "file", "file_id": "films.jsonl", "reason": "..."}`).
Воркер читает их из каталога `GV_IMPORTS__FILES_DIR` (по умолчанию `seeds/catalog`).

- `films.jsonl` — фильмы (~20), `tracks.jsonl` — треки (~20). Только метаданные: **аудио здесь нет** и не будет — аудио загружается в media-pipeline правообладателем или администратором (ADR 0014).
- `file_id` — только имя файла вида `<имя>.jsonl` (`^[a-z0-9][a-z0-9_-]{0,62}\.jsonl$`), без путей.

## Формат JSONL

Одна строка — одна единица (UTF-8, JSON-объект). Пустые строки и строки, начинающиеся с `#`, пропускаются. Ошибка в строке не прерывает импорт: строка учитывается в `counters.failed`.

| Поле | Тип | Обяз. | Описание |
|---|---|---|---|
| `key` | string ≤ 190, `^[a-z0-9][a-z0-9_:\-.]*$` | да | Стабильный ключ строки (`film:interstellar`). Повторный импорт с тем же ключом обновляет ту же единицу (внешний id `seed`, в события не публикуется) |
| `kind` | `"film"` \| `"track"` | да | Вид единицы (область выводится: `film` → `film`, `track` → `music`) |
| `title` | string 1..300 | да | Основное название (алиас вида `title`) |
| `original_title` | string ≤ 300 | нет | Оригинальное название (алиас вида `alt`) |
| `year` | int 1870..текущий+2 | нет | Год выпуска |
| `aliases` | `[{text, kind?, lang?}]` ≤ 50 | нет | Доп. алиасы; `kind`: `title`/`alt`/`translit`/`short` (по умолчанию `alt`), `lang` — ISO 639-1 |
| `persons` | `[{name, role, key?}]` ≤ 50 | нет | `role`: `artist`/`director`/`actor`; порядок внутри роли — порядок в массиве. `key` — стабильный id персоны (иначе персона сопоставляется по имени) |
| `genres` | `[string]` ≤ 20 | нет | Жанры (приводятся к slug в нижнем регистре) |
| `external_refs` | `{tmdb?, musicbrainz?, isrc?, spotify?, yandex_music?}` | нет | Внешние id (строки). Если единица с таким id уже есть — она обновляется |
| `metrics` | `{popularity?, vote_average?, vote_count?, revenue_usd?, runtime_min?, duration_ms?}` | нет | Неотрицательные числа, `vote_average` 0..10 |
| `countries` | `[string]` ≤ 20 | нет | Страны производства, ISO 3166-1 alpha-2 (`US`, `ru` → `RU`) |
| `clue_texts` | `{plot_raw?, plot_masked?, tagline?, emoji?}` | нет | Тексты загадок `description`/`emoji` (ADR 0024): `plot_raw` ≤ 5000 (маскируется автоматически, если нет `plot_masked`), `plot_masked` ≤ 2000, `tagline` ≤ 300, `emoji` ≤ 12 графем. Попадают в черновик; с `GV_CATALOG__SEED_TEXTS_APPROVED=true` (только local/ci) — сразу одобрены и публикуются |
| `source_media` | `[{kind, origin, uri, position?, source_url?, license_note?}]` ≤ 20 | нет | Исходники по адресу: `kind` — `audio`/`frame`/`poster`/`soundtrack`/`audio_clip`, `origin` — `upload`/`tmdb`, `uri` — `https://…`, `position` 1..100 (кадры `frame_sequence`). Вне local/ci без `source_url` и `license_note` строка отклоняется (D6) |

Неизвестные поля — ошибка строки (опечатки не теряются молча).
Транслит-алиасы (`interstellar` ↔ `интерстеллар`) строятся автоматически.
Ручные алиасы модератора повторный импорт не перетирает.

Пример:

```json
{"key": "film:interstellar", "kind": "film", "title": "Интерстеллар", "original_title": "Interstellar", "year": 2014, "persons": [{"name": "Кристофер Нолан", "role": "director"}], "genres": ["фантастика"], "external_refs": {"tmdb": "157336"}}
{"key": "track:bohemian-rhapsody", "kind": "track", "title": "Bohemian Rhapsody", "year": 1975, "persons": [{"name": "Queen", "role": "artist"}], "metrics": {"duration_ms": 354000}}
```
