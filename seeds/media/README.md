# Синтетические исходники ассетов

Тоны для треков и абстрактные кадры для фильмов из `seeds/catalog` — для local и e2e
(план этапа 2, §5 «Сиды»). Прав третьих лиц нет (ADR 0014): звук и картинка генерируются
из ключа единицы каталога, поэтому бинарные файлы в git не хранятся.

```bash
uv run python seeds/media/generate.py          # → seeds/media/out/
```

Генератор читает все `seeds/catalog/*.jsonl`. Получится (имя — ключ строки каталога с `-`
вместо `:`):

| Файл | Единица | Вид исходника (`kind`) | Загадки |
|---|---|---|---|
| `track-<slug>.m4a` (50 с, AAC) | трек | `audio` | `audio_intro`, `audio_reverse` (`start_s` 30) |
| `film-<slug>.jpg` (1280×720) | фильм | `frame` | `frame_progressive`, ревил `audio_blackout` |
| `film-<slug>-{1..6}.jpg` | фильм | `frame`, `position` = номер | `frame_sequence` (1 — самый сложный) |
| `soundtrack-<slug>.m4a` (20 с) | фильм | `soundtrack` | `soundtrack` |
| `clip-<slug>.m4a` (20 с) | фильм | `audio_clip` | `audio_blackout` |

Тексты `description`/`emoji` — не файлы: они приходят из catalog (`clue_texts` строки
импорта, одобряются сразу при `GV_CATALOG__SEED_TEXTS_APPROVED=true`, только local/ci).

## Порядок загрузки (local)

1. `gv dev up infra`, `gv dev up services`.
2. Импорт каталога через admin: `POST /admin/v1/imports {"source": "file", "file_id": "tracks.jsonl", "reason": "сиды"}`
   и то же для `films.jsonl` (см. `seeds/catalog/README.md`). catalog публикует `MediaItemCreated`.
3. Для каждой единицы найти `media_item_id` в админке (карточка единицы) и загрузить файлы:
   `POST /admin/v1/sources` (media-pipeline, multipart: `media_item_id`, `kind` из таблицы
   выше, `position` для кадров последовательности, `reason`, `file`; заголовок
   `Idempotency-Key`). Вне local/ci обязательны `origin`, `source_url`, `license_note` (D6).
4. media-pipeline генерирует наборы по профилям `default` и публикует `AssetsReady`;
   game заполняет проекцию `clue_assets`.
5. game публикует сиды режимов (`seeds/game/modes`, только local/ci, `GV_GAME__BOOTSTRAP_MODES_DIR`)
   и создаёт наборы дня периодической задачей `game.daily.schedule`. Если пул ещё пуст,
   наборы получают статус `failed` и пересоздаются на следующем запуске задачи
   (`GV_GAME__SCHEDULE_INTERVAL_S`) или при первом старте дейлика.

Минимум пула для публикации конфига — `source.params.pool.min_playable` в сидах режимов
(10 для дейликов, 20 для случайных): загрузите исходники хотя бы для такого числа единиц.
