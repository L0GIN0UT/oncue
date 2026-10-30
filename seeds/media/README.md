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
| `soundtrack-<slug>.m4a` (50 с) | фильм | `soundtrack` | `soundtrack` |
| `clip-<slug>.m4a` (50 с) | фильм | `audio_clip` | `audio_blackout` |

Тексты `description`/`emoji` — не файлы: они приходят из catalog (`clue_texts` строки
импорта, одобряются сразу при `GV_CATALOG__SEED_TEXTS_APPROVED=true`, только local/ci).

## Засев одной командой (local/ci)

```bash
gv dev up infra && gv dev up services          # стек с dev-оверлеем (порты 127.0.0.1:81xx)
export GV_BOOTSTRAP_ADMIN__LOGIN=... GV_BOOTSTRAP_ADMIN__PASSWORD=... GV_SERVICE_SECRET_ADMIN=...  # из infra/compose/.env
uv run gv dev seed [--date YYYY-MM-DD]
```

`gv dev seed` (`tools/gv/src/gv_cli/seed.py`) импортирует все файлы каталога, генерирует
исходники и загружает на фильм шесть кадров (`position` 1..6), саундтрек и звук фильма, на
трек — тон (`origin=seed`); ждёт готовности всех наборов единицы и статических профилей,
играбельного пула каждого режима в проекции game и перегенерирует наборы всех плановых
режимов на текущий период: дейлики — на день, `film.bracket.weekly` — на неделю (с
понедельника), с проверкой пригодности (у сетки — тема и 32 единицы). Повторный запуск
безопасен (стабильные `Idempotency-Key`). Основной кадр `film-<slug>.jpg` сидер не грузит:
`frame_progressive` берёт самый свежий кадр вида `frame`.

## Порядок загрузки вручную (local)

1. `gv dev up infra`, `gv dev up services`.
2. Импорт каталога через admin: `POST /admin/v1/imports {"source": "file", "file_id": "tracks.jsonl", "reason": "сиды"}`
   и то же для `films.jsonl` (см. `seeds/catalog/README.md`). catalog публикует `MediaItemCreated`.
3. Для каждой единицы найти `media_item_id` в админке (карточка единицы) и загрузить файлы:
   `POST /admin/v1/sources` (media-pipeline, multipart: `media_item_id`, `kind` из таблицы
   выше, `position` для кадров последовательности, `reason`, `file`; заголовок
   `Idempotency-Key`). `origin`, `source_url`, `license_note` необязательны (ADR 0072).
4. media-pipeline генерирует наборы по профилям `default` и публикует `AssetsReady`;
   game заполняет проекцию `clue_assets`.
5. game публикует сиды режимов (`seeds/game/modes`, только local/ci, `GV_GAME__BOOTSTRAP_MODES_DIR`)
   и создаёт наборы дня периодической задачей `game.daily.schedule`. Если пул ещё пуст,
   наборы получают статус `failed` и пересоздаются на следующем запуске задачи
   (`GV_GAME__SCHEDULE_INTERVAL_S`) или при первом старте дейлика.

Минимум пула для публикации конфига — `source.params.pool.min_playable` в сидах режимов
(10 для дейликов, 20 для случайных): загрузите исходники хотя бы для такого числа единиц.

## Реальные открытые медиа (только local)

Дополнение к синтетике, ничего не удаляет. Файлы лежат **вне репозитория** (ADR 0014), по
умолчанию в `C:/gv-media`:

```bash
uv run python seeds/media/fetch_tmdb.py        # фильмы TMDB → tmdb/ (ключ TMDB в .keys.env)
uv run python seeds/media/fetch_fma.py --count 1000   # клипы FMA по 30 с → fma/clips
uv run python seeds/media/fetch_jamendo.py --count 400  # Jamendo: клипы 30 с + обложки → jamendo/
uv run gv dev seed-real --real-dir C:/gv-media --import-dir <каталог seeds/catalog стенда> \
    --ffmpeg <путь к ffmpeg>                   # переменные админа — как у `gv dev seed`
```

Что делает `gv dev seed-real` (`tools/gv/src/gv_cli/seed_real.py`):

- пишет `real-films.jsonl` и `real-tracks.jsonl` в `--import-dir` (это каталог, который
  смонтирован в catalog; в git не попадают) и импортирует их; `overview` фильма уходит в
  `clue_texts.plot_raw` (загадка «описание»), `listens` трека — в `metrics.popularity`;
  poster/frames/audio/cover/album/license/origin/source_url в импорт не попадают;
- читает три источника в `<real-dir>`: `tmdb/` (фильмы), `fma/` и `jamendo/` (треки; нет папки —
  источник пропускается). Треки Jamendo без клипа на диске отбрасываются, без файла обложки
  идут без неё; жанры приводятся к общему написанию (`hiphop` → `hip-hop`), `--tracks-limit`
  действует на каждый источник отдельно;
- перекодирует клипы в `<real-dir>/seedpack/audio/*.m4a`: ffmpeg зацикливает 30 секунд до
  50 с (профили читают `start_s` 30 у реверса и до 15 с уровня), как у синтетики;
- собирает недостающие файлы в zip с `manifest.json` (пачками `--package-units`, по умолчанию
  100 единиц; `ZIP_STORED`: медиа уже сжато), отправляет `POST /admin/v1/packages` и ждёт статус
  `GET /admin/v1/packages/{id}`: `audio` и, у треков Jamendo, `cover` (обложка альбома,
  роль манифеста 1.1) на трек, шесть `frame` (`position` 1..6) и `poster` на фильм; уже загруженное пропускает; ждёт наборы ассетов. Приём файлов и проверки архива — на
  стороне media-pipeline (`services/media_pipeline/CLAUDE.md`), клиент остаётся тонким;
- собирает сборники: по жанрам (до 6, 20–40 треков, `--min-collection-size`), «Топ по
  рейтингу», «Фильмы 80-х», «Нулевые», «Мультфильмы» и публикует их; обновляет наборы периода.

**Картинка ответа (`artwork`, ADR 0073).** Обложка и постер дают набор `artwork` (600×600 у
трека из `cover`, 600×900 у фильма из `poster`, а без постера из кадра), его ждёт тот же шаг
«наборы ассетов готовы». У треков FMA обложек нет, у них `Reveal.artwork` остаётся `null`
(заглушку рисует фронтенд). Происхождение Jamendo: `origin=upload`, страница трека — в
`source_url`, лицензия (`license_ccurl`) — в `license_note`: контракт манифеста значения
`jamendo` не знает и не расширяется. Часть треков под NC/ND — только для локального стенда
(см. раздел про Jamendo ниже). Треки Jamendo без прослушиваний (`popularity` нет) в жанровые
сборники попадают после треков FMA — по ним сборник берёт самых популярных; в пул режимов
(дейлики, случайные) они входят наравне.

## Правка профиля ассетов на уже засеянном стенде

Параметры профилей лежат в `services/media_pipeline/src/media_pipeline_service/config/asset_profiles.yaml`
(кадры - `frame_progressive/default`: размытие 25, 20, 15, 10, 5, 0 px). Правка меняет
`params_hash`, поэтому у уже загруженных единиц нужны новые наборы:

```bash
uv run gv dev replan-assets --clue-type frame_progressive   # переменные админа - как у `gv dev seed`
```

Одна команда: порциями досоздаёт недостающие наборы (`POST /admin/v1/asset-sets/replan`), ждёт их
готовности и новых версий режимов game (`game.modes.bootstrap` публикует правку сида, когда под
новый хэш готов пул; в dev-оверлее проверка раз в минуту). Старые наборы и версии режимов остаются:
начатые сессии и наборы дня доигрываются по ним. Повтор безопасен. Сначала должны быть выкачены
новые образы media-pipeline и game.

У реальных фильмов нет `soundtrack` и `audio_clip`, поэтому режимы «саундтрек» и «затемнение»
по-прежнему играют на синтетике. Повторный запуск безопасен (стабильные `Idempotency-Key`).

## Jamendo: клипы и обложки (`fetch_jamendo.py`)

Выгрузка треков [Jamendo](https://developer.jamendo.com) (API v3.0) в `<root>/jamendo/`:
`clips/<id>.mp3`, `covers/<album_id>.jpg` (600 px, один файл на альбом) и
`catalog-tracks.jsonl` (формат как у FMA, плюс `source_url`, `origin: "jamendo"`, `cover`).
Ключ — `JAMENDO_CLIENT_ID` в окружении или в `<root>/.keys.env`.

- Берётся `--count` треков (по умолчанию 400) с равной квотой на ~12 жанров, по популярности;
  только с разрешённым скачиванием, с названием (< 60 символов), исполнителем, обложкой и жанром.
- Трек скачивается целиком, ffmpeg режет клип 30 с (с 30-й секунды, у треков короче 60 с — с
  середины), исходник удаляется. Повторный запуск не качает готовое заново.
- `+` в `include` нужно кодировать как `%2B`, иначе API отдаёт пустой список; пустые ответы
  на живой запрос случаются — скрипт повторяет.

**Лицензии.** `license_ccurl` сохраняется как есть и не фильтруется: часть треков под
`NC` (некоммерческие) и `ND`. Для локального стенда это допустимо, для публичного релиза
условия каждой лицензии нужно проверить (и отфильтровать или получить права).
