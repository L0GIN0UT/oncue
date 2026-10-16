# Кураторские сборники засева

Сиды кураторских сборников этапа 3b (план `docs/plans/stage-3b.md` §0 п. 1, D14). Их создаёт
`uv run gv dev seed` после каталога и ассетов: `POST /admin/v1/collections` (права
`admin.access` + `collections.curate`, у bootstrap-админа — с миграции identity `0007`).
Если сборник с таким названием уже есть и содержимое изменилось, выпускается новая версия
(`PUT /admin/v1/collections/{id}` с `base_version`). Без изменений засев сборник не трогает.

Засев ждёт три вещи:
1. единицы в проекции collections (пока там нет единицы, ответ — 422
   `collections.item_unavailable`);
2. наборы своих эмодзи со `scope = collection:<id>` в media-pipeline
   (`GET /admin/v1/asset-sets?media_item_id=` → `scope`);
3. проекцию сборника в game: `GET /v1/collections/{id}/modes`, режим по умолчанию
   `available`, играбельны все единицы.

Адрес collections — `GV_SEED__COLLECTIONS_URL` (по умолчанию `http://127.0.0.1:8106`).

## Формат `<key>.yaml`

| Поле | Обяз. | Описание |
|---|---|---|
| `key` | да | Ключ засева, совпадает с именем файла. Входит в `Idempotency-Key` мутаций |
| `title` | да | Название (≤ `GV_COLLECTIONS__LIMITS__TITLE_MAX`). По нему засев находит созданный сборник, поэтому смена названия создаёт новый сборник |
| `description` | нет | Описание |
| `domain` | да | `film` / `music` |
| `default_mode_key` | да | Шаблон режима сборника из `seeds/game/modes` (`<domain>.collection.*`) |
| `tags` | нет | Ключи тегов из `services/collections/.../config/meta.yaml` (с подходящим `domains`) |
| `cover` | нет | `id` пресета обложки из того же `meta.yaml` |
| `visibility` | нет | `public` (по умолчанию) / `unlisted` |
| `featured_rank` | нет | Отметить «рекомендуемым» с этим порядком (снять отметку засев не пытается) |
| `items` | да | Ключи сидов каталога (`film:interstellar`) или `{item, emoji}` со своими эмодзи: строго RGI, 6..12 графем |

Контракт с каталогом, словарями collections и режимами game проверяет
`tools/gv/tests/test_seed.py::TestCollectionSeeds`.
