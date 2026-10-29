# План этапа 3a: «Режимы и загадки»

> Статус: **утверждён владельцем 2026-10-16** (решения — в конце документа). Основа: [roadmap](../09-roadmap.md) (этап 3, хвосты этапа 2), [игровой движок](../02-game-engine.md), [план этапа 2](stage-2.md). Сборники и медиатека (collections, library) сюда не входят, они пойдут отдельным планом 3b.
> Новые ADR: 0023–0027 (§2.8).

## 0. Цель и критерии готовности

**Цель.** В браузере можно сыграть шесть новых типов загадок (`audio_reverse`, `audio_blackout`, `soundtrack`, `description`, `emoji`, `frame_sequence`) и три новых режима: марафон, «больше/меньше», топ-32. Всё собирается из конфигов и плагинов ядра, без `if type == ...`. Админ может опубликовать конфиг с новыми параметрами уровней: game запрашивает генерацию ассетов (`ClueAssetsRequested`), media-pipeline их строит. Ответ по-прежнему проверяет только сервер, а клиент получает уровень загадки только после того, как уровень открыт.

**Демонстрация (на локальном compose у владельца и в CI):**
0. *Предусловие.* Живой прогон §0 этапа 2 на compose (`gv dev up services`, `gv dev seed`, `e2e/daily.spec.ts`). Он был хвостом «до слияния», теперь Docker есть. Найденные дефекты исправляются до потоков 3a.
1. `gv dev seed` загружает расширенный синтетический каталог. В нём ≥ 40 фильмов одного синтетического режиссёра и ≥ 40 треков одного артиста, с метриками и одобренными текстами загадок. Загружаются исходники новых видов (`soundtrack`, `audio_clip`, по 6 кадров на фильм). Для всех профилей по умолчанию приходит `AssetsReady`. game публикует сиды новых режимов (§10).
2. Каждый новый тип загадки играется в случайном режиме (`*.random.<clue>`). Уровень `description` и `emoji` приходит как JSON по подписанной ссылке `/media/a/<hex>.json`. Запрос уровня выше текущего возвращает 403. В снимке сессии нет `media_item_id` и названия. Ревил `audio_blackout` показывает кадр фильма.
3. Админ меняет уровни `film.random.description`. Валидация показывает `playable_count = 0` и предупреждение `assets.profile_missing`. Админ нажимает «Запросить ассеты»: уходит `game.ClueAssetsRequested`, в media-pipeline появляется динамический профиль, идут `AssetsReady`, пул растёт, публикация проходит.
4. Марафон: игрок выбирает режиссёра или артиста из подсказок и играет раунды до исчерпания пула или потери всех «жизней» (`session.end`). Итог показывает пройденную часть пула.
5. «Больше/меньше» (кино, `vote_average`): видна метрика опорной единицы, метрика второй скрыта до ответа. Серия идёт до первой ошибки. Итог показывает серию, личный рекорд и процентиль. В вариантах ответа непрозрачные `option_id`, без `media_item_id`.
6. Топ-32: недельная сетка, 31 выбор. После каждого выбора видно «согласны N %». Повторный вход открывает итог. `game.BracketCompleted` валиден по схеме.
7. Ревил, открытый через 2 минуты после конца раунда, загружает финальный кадр или звук (хвост этапа 2).
8. Журнал `link_issuance` чистит периодическая задача `media.link_issuance.purge`: видна метрика `gv_periodic_job_runs_total{job=...}`, из `IssueAssetLink` очистка убрана.
9. Проходят `gv lint`, `gv test --changed` (все уровни), `gv contracts check`, `gv gen --check`, во `frontend/` — lint/typecheck/test/build и Playwright на моках, e2e `daily` + `modes` на compose (локально и в CI).

## 1. Сквозные решения

| # | Решение (рекомендация) | Варианты и почему |
|---|---|---|
| D1 | **Состав раунда — `RoundDraw`.** Source отдаёт `RoundDraw(target, shown, options)` вместо голой единицы. Классические источники оборачиваются адаптером ядра, `daily` и `random` не меняются | Сейчас раунд — это одна единица (`RoundPlan.media_item_id`), а «больше/меньше» и топ-32 показывают две. Отдельный агрегат на каждый режим — дублирование правил |
| D2 | **Формат ответа — плагин `AnswerFormat`** (`classic`, `pick`). `answer.format` в контракте становится ключом плагина, а не enum. Ответ `{option_id}` — непрозрачный id варианта раунда. **`lite` не делаем**: режимам 3a он не нужен, он уйдёт в этап 4 (лайт-ладдеры) или 3b (свои варианты сборника) **[владелец]** | `pick` нужен и паре, и сетке. `lite` требует подбора дистракторов, а потребителя в 3a у него нет |
| D3 | **Завершение сессии — плагин `EndRule`** в `session.end {type, params}`: `all_rounds` (по умолчанию, если поле не задано), `lives {count}`. Марафон — `lives: 3`, пара — `lives: 1`. `session.rounds` становится максимумом, предел в контракте 50 → 200 | Флаги вроде `until_miss` в SessionSpec — путь к `if`. Плагин симметричен Scorer |
| D4 | **Ленивая материализация раундов.** Розыгрыш хранится в `state.plan` (скрыт от клиента), раунд i+1 создаётся после завершения i. Сетка добавляет пары через `ProgressiveSource.followups(...)`. Раннее завершение отбрасывает невыданный план | Если создавать все раунды на старте, будущие пары видны в снимке. Сетке нужны победители прошлого тура |
| D5 | **Тексты загадок (`description`, `emoji`) — это ассеты media-pipeline.** Тексты хранит и курирует catalog (`clue_texts`: черновик → одобрено). Одобренные тексты уходят в `MediaItemUpdated` (новое необязательное поле). media-pipeline генерирует JSON по уровням и выдаёт его по подписанной ссылке **[владелец]** | (b, рекомендуется) единые античит (D11), играбельность по `params_hash` (D7) и `ClueView`, а свои ассеты сборников в 3b пойдут тем же путём. (a) game отдаёт текст из своей проекции: меньше звеньев, но ядру нужен `ClueView` без ассета и вторая модель играбельности. Нужен ADR |
| D6 | **Откуда берутся данные — гибрид.** `description`: импорт TMDB (`overview` ru, `tagline`, `production_countries`, с атрибуцией TMDB) → автоматическая маскировка имён (название, алиасы, персоны) → **одобрение модератором**; админ может написать текст вручную. `emoji`: ручной ввод в админке. Медиа (`soundtrack`, `audio_blackout`, кадры `frame_sequence`): TMDB backdrops для кадров плюс загрузка админом из своих источников. У исходника есть `origin` (`tmdb`, `upload`, `seed`), `source_url` и `license_note`. **Изменено (ADR 0072, решение владельца 2026-10-29):** они необязательны в любой среде — раньше без них загрузка вне local/ci отклонялась; теперь пустое помечается `source_missing`/`license_missing` и ищется фильтром. Режимы со звуком фильмов публикуются только при достаточном пуле (`min_playable`), в local/ci — синтетика | Владелец выбрал гибрид: медиа будет много и из разных открытых источников. Метаданные происхождения позволяют снять единицу по жалобе правообладателя (`MediaItemBlocked`). Юридическая допустимость конкретных источников проверяется владельцем до публичного запуска (ADR 0014). LLM-черновики эмодзи и описаний — позже, отдельным решением |
| D7 | **Ревил ассетом другого вида — `clue.reveal {type, params}`** (необязательно, дополняет контракт v1). game ищет второй набор ассетов по его `params_hash`. Единица играбельна, только если готовы оба набора. Для `audio_blackout` reveal — `frame_progressive/still` | Альтернатива — набор ассетов из двух исходников: ломает `AssetSet.source_media_id` и смешивает виды. Нужен ADR |
| D8 | **`ClueAssetsRequested`.** Админ явно жмёт «Запросить ассеты» на черновике, game публикует событие `{request_id, clue_type, params, params_hash, domain}`. media-pipeline заводит **динамический профиль** (БД, дедупликация по `params_hash`, лимит `GV_MEDIA__MAX_DYNAMIC_PROFILES = 50`) и досоздаёт наборы порциями периодической задачей. Статус профиля админка читает через admin-API media-pipeline (ADR 0013), обратного события нет | Автоматический запрос при каждом сохранении черновика — шторм генерации на опечатках. Синхронный вызов game → media — запрещённая цепочка |
| D9 | **Метрики для «больше/меньше»** берутся из `MediaItemCreated/Updated.metrics` (уже в контракте). game проецирует их в `catalog_items.metrics`. Метрика задаётся в `source.params.metric` с разрешённым списком. Музыка в 3a — `popularity` трека; «слушатели в месяц» — этап 6 (интеграции) **[владелец]** | Существующие строки проекции заполняются командой catalog `RepublishItems`: повтор событий отбрасывается по `version` |
| D10 | **Топ-32 — плановый набор с периодом.** `ScheduledSource.period: day|week`: наборы дня обобщаются до «набора периода» (`daily_sets.period`, `day` = начало периода). Тема недели — персона с ≥ 32 играбельными единицами; её выбирает генератор, админ может заменить. Очков нет (Scorer `none`), статистика «согласны» хранится по паре `(winner, loser)` и считается только для пользователей **[владелец: оставить в 3a или вынести]** | Отдельный сервис или таблица под сетку — дубль курации и календаря дейликов |
| D11 | **Рекорды и процентиль** включаются флагом `features.records` в конфиге. Таблицы `player_records` и `score_histogram` обновляются в транзакции завершения, только для пользователей. Гости играют все новые режимы (`allow_guest`), но без рекордов | Если считать процентиль по сессиям налету — растёт нагрузка. Ключ режима в коде — хардкод |
| D12 | **Подача на фронте выбирается по данным:** `clue.kind` (`audio`, `image`, `text`), `answer.format` (`classic`, `pick`), `result.presentation` (`score`, `streak`, `bracket`, объявляет Scorer). Реестры компонентов — как `clueRegistry` | Ветвление по `mode_key` или `source_type` на клиенте — хардкод режимов |
| D13 | **Ссылка ревила (хвост этапа 2):** фронт сразу скачивает финальный ассет в `Blob`. При ошибке или истечении вызывает новую ручку `POST /v1/sessions/{id}/rounds/{index}/final-clue`: она выдаёт свежую ссылку только для завершённого не-`void` раунда, с лимитом | Увеличить TTL — ослабить ADR 0012. Перезапрос снимка — лишняя выдача ссылок всех раундов |
| D14 | **Марафон по персоне** (артист, режиссёр, актёр — 1..`max_subjects`). Игрок выбирает персону: `GET /v1/modes/{key}/subjects?q=` ищет по проекции game (`person_ids` с GIN-индексом, число играбельных ≥ `min_items`). Франшиза и студия — после появления их в catalog (не 3a) | В catalog нет франшиз в событиях; заводить их ради марафона — работа этапа каталога |

## 2. Шаг 0 — оркестратор (contract-first)

**0a** (ветка `stage3a/contracts`) блокирует потоки. **0b** (`stage3a/seed`) — засев и e2e, идёт параллельно с потоками.

### 2.1. Предусловие, реестр, CI
- Живая демонстрация этапа 2 (§0 п. 0), её дефекты — отдельные `fix/*` в `main`.
- `infra/services.toml` не меняется. Зависимости: новых пакетов нет (ffmpeg `areverse` уже в образе media). `uv.lock` в потоках не меняется.
- `.github/workflows/ci.yml`: в job `e2e-smoke` добавляется `e2e/modes.spec.ts` под тем же `E2E_SEEDED=1`.

### 2.2. `contracts/modes/` (только расширяющие правки v1)
- `mode_config.v1.json`:
  - `answer.format`: enum → `PluginKey`;
  - `session.rounds.maximum` 50 → 200;
  - `session.end?: {type, params}`;
  - `clue.reveal?: {type, params}`;
  - `source.params` подбираются по `type`, как раньше.
- Новые `params/*`:
  - `source.marathon.v1.json` — `roles[]`, `max_subjects 1..10`, `min_items`, `pool`, `order: shuffle|year`;
  - `source.pair.v1.json` — `metric ∈ {vote_average, revenue_usd, runtime_min, year, popularity}`, `min_gap`, `ties: both_correct`, `pool`, `chain_length ≤ 200`;
  - `source.bracket.v1.json` — `size ∈ {8,16,32}`, `period: week`, `timezone`, `schedule_ahead_periods`, `subject {roles[], min_items}`, `pool`;
  - `answer_format.pick.v1.json`, `matcher.metric_compare.v1.json`, `matcher.preference.v1.json`;
  - `scorer.streak.v1.json`, `scorer.none.v1.json`, `end.all_rounds.v1.json`, `end.lives.v1.json`.
- В `source.daily.v1.json` добавляется `period?: day` (по умолчанию `day`).

### 2.3. `contracts/clues/`
- Новые схемы:
  - `audio_reverse.params.v1.json` — `start_s`, `levels[{seconds}]`;
  - `soundtrack.params.v1.json`, `audio_blackout.params.v1.json` — как у `audio_intro`;
  - `frame_sequence.params.v1.json` — `levels[{frame: 1..6, max_width}]`;
  - `description.params.v1.json` — `levels[{fields: [plot_masked|year|genres|country|director|cast_top3|tagline]}]`, поля накапливаются от уровня к уровню;
  - `emoji.params.v1.json` — `levels[{count: 1..12|null}]`.
- `text_asset.v1.json` — формат JSON-ассета `{blocks: [{kind, text|items}]}`.
- `params_hash.golden.json` дополняется векторами новых типов.

### 2.4. События
- `game.ClueAssetsRequested` (топик `game.mode_config.v1`, `aggregate_id = mode_key`): `request_id`, `mode_key`, `draft_revision`, `domain`, `clue_type`, `params`, `params_hash`, `actor_id`, `requested_at`.
- `game.BracketCompleted` (`game.session.v1`): `session_id`, `version`, `user_id|null`, `is_guest`, `mode_key`, `mode_version`, `period_start`, `champion_id`, `picks: [{stage, winner_id, loser_id}]`.
- `GameFinished` v1 не меняется: пара и марафон укладываются в `exact|miss|gave_up|void`, сетка публикует только `BracketCompleted`.
- В `catalog.MediaItemCreated|Updated` добавляются необязательные поля `clue_texts?: {plot_masked?, tagline?, emoji?}` (только одобренные) и `countries?: [iso2]`.
- Примеры — `contracts/events/*/examples/`.

### 2.5. API (0a: схемы и заглушки 501, снимок OpenAPI)
- **game, public:**
  - `POST /v1/sessions` — плюс `subject_ids?: [uuid ≤ 10]`;
  - `GET /v1/modes` — плюс `answer_format`, `start: {presets?, subject?: {roles, max}}`, `presentation`;
  - `GET /v1/modes/{key}/subjects?q=&limit=` → `[{subject_id, name, role, items}]`;
  - в `SessionSnapshot.round` — `shown: [{option_id, title, year, metric?, clue?}]`, `options: [{option_id, label_key?, title?}]`, `progress: {lives_left?, stage?, streak?}`;
  - ответ хода — `{option_id}` и `stats?: {agree_pct}`;
  - `ClueKind` += `text`;
  - `POST /v1/sessions/{id}/rounds/{index}/final-clue` → `ClueOut` (409 `game.round_not_finished`, 410, 429);
  - `GET /v1/me/records?mode_key=` → `[{mode_key, best, percentile, achieved_at}]`;
  - в `result` — `presentation`, `record?: {best, is_new, percentile}`, `bracket?: {champion, picks}`.
- **game, admin:**
  - `POST /admin/v1/mode-configs/{mode_key}/asset-requests {draft_revision, reason}` (`config.modes.publish`, `Idempotency-Key`);
  - `GET .../asset-requests`;
  - `validate` получает предупреждение `assets.profile_missing`;
  - `daily-sets` — плюс параметр `period`.
- **media_pipeline, admin:**
  - `GET /admin/v1/profiles` → `[{clue_type, profile_key|null, params_hash, origin: static|dynamic, status, sets: {ready, pending, failed}}]`;
  - `POST /admin/v1/sources` принимает `kind ∈ {audio, frame, poster, soundtrack, audio_clip}`, `position?` и `origin`, `source_url`, `license_note` (D6; необязательны по ADR 0072);
  - `PUT /admin/v1/sources/{id}/position {position, reason}`.
- **catalog, admin:**
  - `GET|PUT /admin/v1/items/{id}/clue-texts {plot_masked, tagline, emoji, reason}`;
  - `POST .../clue-texts/approve {revision, reason}`;
  - `POST /admin/v1/items/republish {domain?, reason}` (задача `catalog.republish`).
- **bff_web:** `GET /v1/pages/solo` → блоки `daily`, `modes` (сгруппированы по `presentation`/`source_type` для витрины), `weekly`, `records` (личный); `GET /v1/pages/results/{id}` — плюс `presentation`, `record`, `bracket`.

### 2.6. Скелет ядра (0a, `libs/game_kernel`)
- `spec.py`: `AnswerSpec.format: PluginKey`, `SessionSpec.end: PluginSpec | None`, `ClueSpec.reveal: PluginSpec | None`, `rounds ≤ 200`.
- `plugins.py`:
  - `RoundDraw`, `OptionSpec(id, label_key, item)`, `RoundSource.draw_rounds(...)`;
  - `ProgressiveSource.followups(params, history) -> list[RoundDraw]`, `planned_rounds(params)`;
  - `SubjectSource.subject_query(params)`;
  - `AnswerFormat` (`key`, `input_kinds`, `params_schema`, `options(draw)`);
  - `EndRule.should_end(history, params)`;
  - `Scorer.presentation`;
  - `AnswerInput.option_id`.
- `refs.py`: `MediaItemRef.metrics: Mapping[str, float]`, `person_ids`.
- `ports.py`: `PoolQuery.person_ids_any`, `roles`.
- `registry.py`: `register_answer_format`, `register_end_rule`; правила `validate` (§4) — только сигнатуры.

### 2.7. Права и настройки
- Прав не добавляем:
  - запрос ассетов — `config.modes.publish`;
  - тексты загадок — существующее право редактирования единиц catalog (как алиасы);
  - профили media — `admin.access` на чтение.
- `env.example.j2`:
  - `GV_MEDIA__MAX_DYNAMIC_PROFILES`, `GV_MEDIA__BACKFILL_BATCH = 200`, `GV_MEDIA__LINK_PURGE_INTERVAL_S = 600`;
  - `GV_CATALOG__TMDB_FRAMES_MAX = 6`;
  - `GV_GAME_LIMITS__FINAL_CLUE_PER_MIN = 30`, `GV_GAME_LIMITS__SUBJECTS_PER_MIN = 60`.

### 2.8. ADR и документация
- 0023 «Состав раунда, форматы ответа и правила завершения как плагины» (D1–D4, уточняет 0008 и 02-game-engine §2–3).
- 0024 «Тексты загадок — ассеты media-pipeline, курирование в catalog» (D5–D6).
- 0025 «Динамические профили ассетов по `ClueAssetsRequested`» (D8, уточняет 0020).
- 0026 «Ревил ассетом другого типа — `clue.reveal`» (D7).
- 0027 «Плановые наборы с периодом, режимы предпочтения, рекорды» (D10–D11).
- Обновить: `02-game-engine.md` §3–5.3, `architecture/services.md` (game, media-pipeline, catalog), `architecture/security.md` §3.

## 3. Потоки и владение файлами

| Поток | Ветка | Может менять |
|---|---|---|
| **A** ядро | `stage3a/kernel` | `libs/game_kernel/**` (кроме `spec.py`), `libs/testkit/src/gv_testkit/game.py` |
| **B** game | `stage3a/game` | `services/game/**`, `contracts/openapi/game.json`, `seeds/game/**` |
| **M** контент | `stage3a/content` | `services/media_pipeline/**`, `services/catalog/**`, `contracts/openapi/{media_pipeline,catalog}.json`, `seeds/media/**` |
| **C** bff | `stage3a/bff` | `services/bff_web/**`, `contracts/openapi/bff_web.json` |
| **D** фронтенд | `stage3a/web` | `frontend/**` |
| **E** admin | `stage3a/admin` | `services/admin/**` |
| Оркестратор | `stage3a/contracts`, `stage3a/seed` | `contracts/{modes,clues,events}/**`, `spec.py`, `libs/{core,service,testkit}` (кроме `game.py`), `tools/gv/**`, `seeds/catalog/**`, `e2e/**`, `.github/**`, `infra/**` (через `gv gen`), `docs/**` |

Правила — как в этапе 2: правка общего — только через оркестратора, изменение контракта — новый шаг 0.

## 4. Поток A — ядро

| # | Файлы | Действие | Тест | Риск |
|---|---|---|---|---|
| A1 | `builtin/clues.py`, `contracts/clues/*` | `AudioReverseClue`, `SoundtrackClue`, `AudioBlackoutClue` (kind `audio`, домены: reverse — music, остальные — film), `FrameSequenceClue` (`image`), `DescriptionClue`, `EmojiClue` (`text`; emoji — music и film) — наследники `LeveledClue`, отличаются только данными класса | contract: модели ↔ схемы и golden `params_hash`; unit: `levels()`/`hint` | низкий |
| A2 | `engine.py`, `round.py` | `ModeEngine`: `draw_rounds` (адаптер `draw()` → `RoundDraw(target)`), `followups`, `should_end`, `reveal_params_hash` (D7), `options_view`; ход `{option_id}` проверяется `AnswerFormat.input_kinds` | property: раунд завершается ≤ `levels × attempts`; детерминизм `draw_rounds` при одном seed | средний — фасад используют game и будущий match |
| A3 | `builtin/answer_formats.py`, `builtin/matching.py` | `ClassicFormat` (`item`/`text`), `PickFormat` (`option`); `MetricCompareMatcher` (higher/lower против опорной единицы, ничья — `both_correct`), `PreferenceMatcher` (всегда `exact`, сохраняет выбор) | golden `matcher_pick.yaml`: ничьи, нет метрики → единица не играбельна | средний |
| A4 | `builtin/sources.py` | `MarathonSource` (пул ∧ `person_ids_any`, `subject_query`, n = min(пул, rounds)); `PairSource` (цепочка с `|Δmetric| ≥ min_gap`, `shown = [anchor]`, `options = higher/lower`); `BracketSource` (ScheduledSource с `period=week`, посев по seed периода, `followups` — пары победителей тура) | unit: сетка 32 → 31 выбор, турниры 16/8/4/2/1; у пары нет соседей с равной метрикой; `PoolTooSmall` | **высокий** — самая сложная логика этапа |
| A5 | `builtin/scoring.py`, `builtin/end_rules.py` | `StreakScorer` (+1 за exact, `presentation=streak`), `NoneScorer` (`bracket`), у `ByLevel` — `presentation=score`; `AllRounds`, `Lives(count)` | property: серия = число exact до первого miss | низкий |
| A6 | `registry.py` | новые правила `validate`: `answer.format` поддерживается загадкой и источником (pair/bracket — только `pick`); `end` совместим со Scorer (`streak` требует `lives`); `clue.reveal` — известная загадка того же домена; метрика из списка; `bracket.size == planned_rounds + 1`. Ошибки собираются с путями | валидатор на всех сидах + «сломанный конфиг на каждое правило» | средний |
| A7 | `gv_testkit/game.py` | `FakeCandidatePort` с `person_ids_any` и метриками; DSL `.when_pick(option)`, `.then_session_ended()` | — | низкий |

**Готово, когда** `gv test libs/game_kernel` (покрытие ≥ 90 %) и `gv contracts check` зелёные. Для `daily`/`random` поведение не меняется (регресс сидов этапа 2).

## 5. Поток B — game

| # | Файлы | Действие | Тест | Риск |
|---|---|---|---|---|
| B1 | `infrastructure/db/migrations/versions/0004_stage3a.py`, `db/tables.py` | expand-only:<br>• `catalog_items` — `metrics jsonb`, `person_ids uuid[]` GIN;<br>• `clue_assets` без изменений;<br>• `daily_sets.period text default 'day'`;<br>• `player_records`, `score_histogram`, `bracket_pick_stats (mode_key, period_start, winner_id, loser_id, picks)`;<br>• `asset_requests (request_id PK, mode_key, params_hash, status, actor_id, reason)` | integration: миграция вверх и вниз на копии с данными этапа 2 | средний |
| B2 | `domain/session.py` | `Round.options` (`option_id → media_item_id`, хранится на сервере), `shown`, `choice`; `GameSession.plan` (скрыт), `materialize_next`, `append_followups`, `end_by_rule`; `max_score` — по материализованным раундам | unit: инварианты, раннее завершение отбрасывает план, сетка добавляет туры | **высокий** — меняется сериализация `state jsonb`; старые сессии этапа 2 должны читаться (версия схемы состояния) |
| B3 | `domain/projection.py`, `application/commands/projection.py` | проекция `metrics`, `person_ids`; толерантный разбор новых полей событий catalog | contract: consumed-пример с `clue_texts`; устаревшая `version` | низкий |
| B4 | `application/rules.py` | возможности вместо ключей: `RoundSource`, `ProgressiveSource`, `SubjectSource`, `period` у ScheduledSource, `presentation`, `reveal_playable_for` | unit на реальных плагинах | средний |
| B5 | `application/commands/play.py`, `queries/play.py`, `views.py` | старт с `subject_ids`; ход `option_id`; после завершения раунда — `should_end` → `finish` или следующий раунд; `final_view` из reveal-набора; рекорд и гистограмма в транзакции завершения; сетка: upsert `bracket_pick_stats` в транзакции хода (только пользователи), `stats.agree_pct` в ответе | component: марафон (жизни), пара (метрика B скрыта до ответа, рекурсивный обход JSON), сетка (31 выбор, повторный вход → итог), гость без рекордов | **высокий** |
| B6 | `application/commands/play.py`, `api/public/__init__.py` | `FinalClue` (D13): только завершённый не-`void` раунд своей сессии, лимит `FINAL_CLUE_PER_MIN` | **регресс-тест хвоста:** раунд завершён, часы +120 с → свежая ссылка; активный раунд → 409; чужая сессия → 404 | низкий |
| B7 | `application/queries/play.py` (`ListSubjects`) | поиск персон по проекции: имя ILIKE + `person_ids` GIN + число играбельных ≥ `min_items`, лимит 20 | integration: фильтры совпадают с `FakeCandidatePort` | средний — нет trigram-индекса (как хвост этапа 2) |
| B8 | `application/commands/daily.py`, `infrastructure/jobs.py` | наборы с периодом: `day` = начало недели в `timezone`, `schedule_ahead_periods`, блокировка в начале периода; тема сетки выбирается из персон с ≥ size играбельных | component: недельный набор фиксирует `mode_version`, замена в `locked` → 409 | средний |
| B9 | `application/commands/modes.py`, `api/admin/__init__.py` | `RequestClueAssets`: черновик валиден по схеме, набора под `params_hash` нет → строка `asset_requests` + outbox `ClueAssetsRequested` (повтор с тем же ключом — тот же ответ, тот же хэш в статусе `pending` — без нового события); `validate` → `assets.profile_missing` | component: событие валидно по схеме, идемпотентность, нет права → 403 | средний |
| B10 | `seeds/game/modes/*.yaml` | сиды §10 | валидатор ядра в CI | низкий |

**Готово, когда** `gv test game` зелёный (domain ≥ 90 %, application ≥ 85 %) и закрыты проверки §11 п. 1–4.

## 6. Поток M — media-pipeline и catalog

| # | Файлы | Действие | Тест | Риск |
|---|---|---|---|---|
| M1 | `media_pipeline_service/infrastructure/jobs.py` (новый), `application/commands/links.py`, `main.py` | **хвост:** `PeriodicJob("media.link_issuance.purge", GV_MEDIA__LINK_PURGE_INTERVAL_S)` в relay; очистка из `IssueAssetLink` убрана | `run_periodic_job`: строки старше retention удалены порциями; `IssueAssetLink` больше не вызывает `purge` | низкий |
| M2 | `domain/sources.py`, `migrations/versions/0005_sources_kinds.py` | `SourceKind` += `soundtrack`, `audio_clip`, `text`; `FAMILY_BY_KIND` += `TEXT`; у `source_media` — `position int`, `origin`, `source_url`, `license_note` (D6; необязательны по ADR 0072); толерантный разбор неизвестного `kind` (лог и пропуск) | unit: сигнатуры, неизвестный вид не роняет consumer | средний |
| M3 | `domain/clues.py`, `application/generators/plugins.py`, `infrastructure/container.py`, `config/asset_profiles.yaml` | генераторы:<br>• `AudioReverse` (`-ss/-t` до `-i`, затем `areverse`);<br>• `Soundtrack`, `AudioBlackout` (логика `audio_intro` с другим `source_kind`);<br>• `FrameSequence` (N исходников `frame` по `position`, нужно ≥ число уровней);<br>• `Description`, `Emoji` (JSON по `text_asset.v1`).<br>Профили `default` + `frame_progressive/still`, `audio_intro/preview` (одноуровневые, для пары и сетки) | unit: отрывок reverse не содержит звука после `start_s + seconds`; уровни `description` накапливают поля; integration ffmpeg | средний |
| M4 | `application/commands/catalog.py` | `PlanAssetSets`: генератор объявляет `source_kind` и `source_count`, вместо `best_source` — `sources_for(profile)`; `clue_texts` из события → источник `text` (хэш содержимого, изменение → перегенерация) | integration: правка текста → новое поколение, `AssetsReady` | средний |
| M5 | `domain/profiles.py`, `application/commands/profiles.py` (новый), `migrations/versions/0006_dynamic_profiles.py`, `infrastructure/jobs.py` | `asset_profiles_dynamic`; consumer `ApplyClueAssetsRequested` (inbox, проверка `params` генератором, лимит, дедупликация по хэшу); каталог профилей = статические ∪ динамические; задача `media.profiles.backfill` порциями `BACKFILL_BATCH` | component: событие → профиль → наборы → `AssetsReady`; повтор события — no-op; лимит → профиль `rejected` | **высокий** — нагрузка ffmpeg, объём S3 |
| M6 | `api/admin/__init__.py` | `GET /admin/v1/profiles`, новые виды и `position` в `sources` | component: права, `Idempotency-Key` | низкий |
| M7 | `catalog_service/domain/clue_texts.py`, `media_item.py`, `migrations/versions/0004_clue_texts.py` | `ClueTexts` (`plot_raw`, `plot_masked`, `tagline`, `emoji` ≤ 12 графем, `status draft|approved`, `revision`), `countries`; `mask_plot` (название, алиасы, имена персон → «…»); в событие идут только одобренные | unit: маскировка (golden), правка после одобрения → снова `draft` | средний |
| M8 | `infrastructure/clients/tmdb.py`, `application/commands/imports.py` | TMDB: `overview` (ru), `tagline`, `production_countries`, до `TMDB_FRAMES_MAX` backdrops с `position`; файловый импорт принимает `clue_texts` и `metrics` | integration на записанных ответах TMDB | средний |
| M9 | `application/commands/admin.py`, `api/admin` | `clue-texts` (GET/PUT/approve), `republish` (задача RabbitMQ, порции, `version+1`) | component: одобрение публикует `MediaItemUpdated` с `clue_texts`, republish идемпотентен по ключу | средний |

**Порядок внутри M:** сначала толерантные потребители (M2, M4), потом продюсер (M7–M9).

## 7. Поток C — bff-web
- `application/queries/solo.py`, `results.py`; `infrastructure/clients/game.py`: блоки `modes` (по `presentation` и `start`), `weekly` (статус сетки периода), `records` (личный, `no-store`); в итоге — `presentation`, `record`, `bracket`.
- Деградация по блокам, как в этапе 2. Тесты: component на `stub_service("game")`; game недоступен → `unavailable`, ответ 200; гость без `records`. Риск низкий.

## 8. Поток D — фронтенд

| # | Файлы (`frontend/src/...`) | Действие | Тест |
|---|---|---|---|
| D1 | `features/game/clues/TextClue.tsx`, `registry.ts`, `shared/api/schema/*` (`pnpm gen:api`) | `text`: JSON-ассет в `Blob` → блоки по реестру `plot`/`fields`/`emoji`; эмодзи крупно, с `aria-label`; `audio_blackout` — `AudioClue` на чёрной сцене (по `meta`) | vitest + MSW, axe |
| D2 | `features/game/answers/{registry.ts,PickOptions.tsx}`, `GameScreen.tsx` | реестр по `answer.format`: `classic` → `AnswerBox`, `pick` → карточки или кнопки (клавиши 1/2, ←/→); `shown` с метрикой опорной единицы; `progress` (жизни, серия, тур) | редьюсер, клавиатура |
| D3 | `features/solo/{SoloHub.tsx,SubjectPicker.tsx}`, `app/play/[modeKey]/page.tsx` | витрина по `start`: пресеты или выбор персоны (combobox на `/subjects`, debounce 200 мс) | Playwright: марафон с выбором режиссёра |
| D4 | `features/game/BracketProgress.tsx`, `RevealCard.tsx` | туры 16→1, «согласны N %» после выбора; финальный кадр сразу в `Blob`, при `error` → `POST final-clue` (один повтор) | **регресс хвоста:** мок отдаёт 403 на старую ссылку → кадр загружен со второй |
| D5 | `features/results/presenters/{score,streak,bracket}.tsx` | реестр по `presentation`; шэр-текст для серии и чемпиона | unit шэр-текстов |
| D6 | `e2e/{clues,marathon,pair,bracket,reveal-expiry}.spec.ts`, `e2e/mocks/` | Playwright на моках по контрактам, axe без нарушений уровня serious | — |

Риски: автовоспроизведение реверса и саундтрека — только по жесту, как в этапе 2. `prefers-reduced-motion` — для смены кадров `frame_sequence`.

## 9. Поток E — admin
- `apps/game_config/views.py`, `templates/`: кнопка «Запросить ассеты» (`reason`, скрытый `idempotency_key`, аудит) и панель статуса профиля (клиент media `GET /admin/v1/profiles`); предупреждения валидации.
- `apps/game_daily/`: календарь показывает периоды `week` и тему сетки, замена единицы и темы.
- `apps/catalog/`:
  - редактор текстов загадок: исходный сюжет рядом с маскированным, emoji, кнопка «Одобрить»;
  - «Переопубликовать единицы» (подтверждение и `reason`);
  - загрузка исходников новых видов и порядок кадров.
- Тесты: pytest-django на `stub_service` (game, media, catalog); нет права → 403 и пункт меню скрыт; аудит пишется и при отказе сервиса. `mypy` с плагином.

## 10. Засев, e2e, демонстрация (оркестратор, 0b)
- `seeds/catalog/synthetic/{films,tracks}.jsonl`: ≥ 40 + 40 единиц «Синтетический режиссёр/артист», метрики без ничьих по каждой метрике пары, синтетические `clue_texts` (одобряются при файловом импорте — только local/ci, флаг `GV_CATALOG__SEED_TEXTS_APPROVED`).
- `seeds/media/generate.py`: `soundtrack-*.m4a`, `clip-*.m4a` (тоны), `film-<slug>-{1..6}.jpg`. Прав третьих лиц нет (ADR 0014).
- `seeds/game/modes/`: `music.random.reverse`, `film.random.{description,emoji,soundtrack,blackout,sequence}`, `music.random.emoji`, `music.marathon.artist`, `film.marathon.director`, `film.pair.rating`, `music.pair.popularity`, `film.bracket.weekly`.
- `tools/gv/src/gv_cli/seed.py`: новые виды загрузки и `position`, ожидание профилей, перегенерация наборов **всех** ScheduledSource (день и неделя), не только `daily`. Тест — `tools/gv/tests/test_seed.py`.
- `e2e/modes.spec.ts`: по одному сценарию на каждый режим, `description`, ревил через 2 минуты (часы Playwright) и `ClueAssetsRequested` через admin-API. Прогон — локально у владельца и в CI.

## 11. Обязательные проверки безопасности
1. **Античит:**
   - до ревила в ответах нет `media_item_id`, названия загаданной единицы и метрики скрытой единицы пары (рекурсивный обход JSON);
   - варианты ответа — непрозрачные `option_id`;
   - `state.plan` не попадает в снимок;
   - JSON-ассеты текста выдаются только для открытого уровня.
2. **IDOR:** `final-clue`, `subjects`, `records` — только свой субъект; чужая сессия → 404.
3. **Целостность хода:** `expected_version`, `Idempotency-Key`, `FOR UPDATE` для `option_id`; upsert статистики сетки в той же транзакции; гостей нет в статистике и рекордах.
4. **Лимиты:** `final-clue`, `subjects`, старт сессий; лимит ссылок media остаётся fail-closed.
5. **`ClueAssetsRequested`:** `params` проверяют обе стороны по одной схеме, размеры ограничены (≤ 20 уровней, ≤ 60 с); лимит динамических профилей; только право `config.modes.publish`; аудит.
6. **Тексты загадок:** в событие и ассет попадают только одобренные; emoji и тексты экранируются на фронте (без `dangerouslySetInnerHTML`); ограничение длины в catalog.
7. **Admin-API** (catalog, media, game): `admin.access` + право, `reason`, `Idempotency-Key`, аудит.
8. **Процедура:** B, M, D, E — `security-discipline`; новые OpenAPI — `42crunch-audit`; M5 (генерация по событию) — `idempotency-and-side-effects`; миграции — `review-migration`.

## 12. Миграции и совместимость
- Все миграции expand-only: game `0004`, media `0005`–`0006`, catalog `0004`. Откат не теряет данных этапа 2.
- `state jsonb` сессий: поле `state_schema` (отсутствует → v1), чтение v1 покрыто тестом на снимке сессии этапа 2.
- Контракт конфига — только расширения v1 (`end`, `reveal` необязательны, `format` расширен). Сиды этапа 2 валидны без правок.
- События: новые поля catalog необязательны, потребители становятся толерантными **до** продюсера. При выкладке сначала game и media, потом catalog.
- Для старых строк проекции после выкладки один раз выполняется `republish` (чек-лист выкладки).

## 13. Что НЕ входит
- collections и library, source `collection` в game, свои ассеты сборников, «смотреть позже» из ревила — **3b**.
- Формат `lite`, дистракторы — этап 4 или 3b (D2). Онлайн, `party_speed`, ладдеры — этап 4.
- LLM-генерация эмодзи и описаний; франшизы и студии в марафоне; «слушатели в месяц» — этапы каталога и 6.
- Награды за рекорды и серии, лента и прогнозы дейлика — этап 5. Детектор аномалий — этап 5.
- Форма редактора по JSON Schema — этап 5. OG-карточки шэра — этап 7.

## 14. Риски

| Риск | Мера |
|---|---|
| Ленивая материализация и сериализация `state` ломают сессии этапа 2 | Версия схемы состояния, регресс-тест на сохранённом снимке, A2/B2 сливаются первыми |
| Сетка — самая объёмная механика (туры, статистика, недельный период) | Отдельные коммиты B8/A4; при нехватке времени выносится без влияния на остальное (вопрос 3) |
| Динамические профили перегружают ffmpeg и S3 | Явный запрос (не автоматический), лимит профилей, порции backfill, метрика длины очереди |
| Мало играбельного контента с правами (звук фильмов, саундтреки) | Синтетика для e2e, `min_playable`, режимы с аудио фильмов публикуются только при наличии пула |
| Маскировка сюжета пропускает имена персонажей | Обязательное одобрение модератором, в событие идут только одобренные |
| Горячая строка `bracket_pick_stats` у популярной сетки | Масштаб этапа приемлем; позже — счётчики в Redis со сбросом (зафиксировать в ADR 0027) |
| Поиск персон без trigram-индекса | Лимит 20, GIN по `person_ids`; trigram — вместе с хвостом этапа 2 |
| Расширение enum в ответах game (`ClueKind`) ловит `gv contracts check` | Единственный клиент — свой фронт с генерированными типами; согласовать как допустимое расширение v1 в 0a |

## 15. Порядок слияния
1. Исправления после живого прогона этапа 2 → `main`.
2. **0a** `stage3a/contracts`, все ветвятся от неё.
3. **A** (ядро), затем rebase B.
4. **M** (сначала толерантные потребители, затем генераторы, catalog, динамические профили).
5. **B** (component-тесты на реальных плагинах и профилях M).
6. **0b** (засев) — нужен B и M.
7. **C**, затем **E**.
8. **D** последним: e2e `modes` на полном стеке, живая демонстрация §0.

После каждого слияния: `uv lock --check`, `gv gen --check`, `gv contracts export && gv contracts check`, `gv test --changed`, `gv test frontend` при изменениях фронта. В конце — обновить `09-roadmap.md` (итог 3a, хвосты), принять ADR 0023–0027.

---

**Решения владельца (2026-10-16):**
1. Этап 3 режется на 3a (режимы и загадки) и 3b (collections + library).
2. D5: тексты `description`/`emoji` выдаются ассетами media-pipeline.
3. D6: контент — гибрид (TMDB-тексты и кадры + загрузки админа с метаданными происхождения).
4. D10: топ-32 остаётся в 3a последним шагом; при перегрузе выносится без влияния на остальное.
5. D2: `lite` не делаем (этап 4 или 3b); D9: музыка в «больше/меньше» — `popularity` трека; D14: марафон только по персонам.
