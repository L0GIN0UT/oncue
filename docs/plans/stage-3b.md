# План этапа 3b: «Сборники и медиатека»

> Статус: **утверждён владельцем 2026-10-16** (решения — в конце документа, вопросы закрыты по рекомендациям). Основа: [roadmap](../09-roadmap.md) (этап 3), [план 3a](stage-3a.md) (его структура и хвосты), [сервисы](../architecture/services.md) §7–8, [концепция](../01-concept.md) §5, §7, §8, [движок](../02-game-engine.md) §5.1.
> Новые ADR: 0028–0031 (§2.9). Ветка-база: `stage3a/integration`.

## 0. Цель и критерии готовности

**Цель.** Зарегистрированный пользователь собирает сборник в конструкторе: выбирает домен, единицы каталога и свои эмодзи-загадки. Черновик сохраняется с защитой от конфликта устройств, затем публикуется. Другие игроки находят сборник в каталоге (поиск, фильтры, сортировки), играют его в game через source `collection` (5/10/25/все), ставят лайк, добавляют в избранное и получают отметку «пройден». У сборника считается сложность. Из ревила, итога и карточек единица сохраняется в медиатеку: «смотреть позже», «просмотрено», «любимое», свои полки. Админ создаёт кураторские сборники, отмечает рекомендуемые, скрывает сборник и ограничивает автора. Ответ по-прежнему проверяет только сервер, а список единиц сборника до прохождения не раскрывается.

**Демонстрация (локальный compose у владельца и CI):**
1. `gv dev seed` поднимает сиды режимов `*.collection.*` и 3 кураторских сборника (через admin-API collections), а также синтетику 3a.
2. Пользователь A создаёт сборник «Кино · кадры» (8 фильмов, у двух свои эмодзи). Он правит черновик в двух вкладках: вторая вкладка получает 409 `collections.draft_conflict` и экран выбора «моя версия / с другого устройства». После этого A публикует сборник. Уходит `collections.CollectionPublished`, game и media-pipeline его проецируют, для своих эмодзи приходит `AssetsReady` со `scope`.
3. Пользователь B находит сборник поиском («кадры», фильтр `film`, сортировка «новые»). Страница сборника показывает число единиц, режимы с «N из M играбельны», сложность и автора. Списка единиц на странице нет. B играет 5 раундов в `film.collection.frame` и 8 раундов в `film.collection.emoji` (свои эмодзи автора).
4. После `GameFinished` (с `collection_id`) B видит отметку «пройден», и ему открывается список единиц. Лайк и избранное работают идемпотентно. Сложность пересчитывается, самоигра автора в неё не входит.
5. В ревиле B жмёт «Сохранить → Смотреть позже», затем на итоге — «Сохранить → Просмотрено» (единица уходит из «смотреть позже» по правилу `exclusive_with`). `/library` показывает полки и свою полку «Ужасы 80-х».
6. Админ скрывает сборник A (`reason`, аудит). Сборник пропадает из каталога, старт сессии в game возвращает 404, автор видит плашку «скрыт модератором». Блокировка единицы в catalog (`MediaItemBlocked`) убирает её из сборника (новая версия).
7. Гость играет публичный сборник, но не может создать сборник, поставить лайк или сохранить единицу: кнопка ведёт на регистрацию.
8. Хвосты 3a: админ выбирает тему недели поиском персоны, а не вводом UUID, и видит список исходников единицы с порядком кадров. Марафон на 60+ раундов публикует валидный `GameFinished` (дефект 3a, §2.3).
9. Проходят `gv lint`, `gv test --changed`, `gv contracts check`, `gv gen --check`, проверки `frontend/` (lint, typecheck, test, build, Playwright на моках) и e2e `daily` + `modes` + `collections` на compose.

## 1. Сквозные решения

| # | Решение (рекомендация) | Варианты и почему |
|---|---|---|
| D1 | **Модель сборника: агрегат, неизменяемые версии и один черновик.** `collections` (статус `draft/published/hidden/deleted`, `kind ugc/curated`, `visibility public/unlisted`) + `collection_versions` (неизменяемый снимок body) + `collection_drafts` (одна строка на сборник, `revision`). Правка опубликованного сборника идёт через черновик поверх версии, публикация создаёт `version+1` | Повторяет паттерн `mode_configs`/`mode_config_drafts` из game. Хранить всё в одной строке нельзя: сессия game фиксирует `collection_version`, нужна неизменяемость |
| D2 | **Конфликт устройств: оптимистичная блокировка.** `PUT /v1/collections/{id}/draft {base_revision, body, client_label}` + `Idempotency-Key`. При расхождении ревизий — 409 `collections.draft_conflict`, в `details.current` лежат `revision`, `updated_at`, `client_label`, `body`. Клиент сохраняет автоматически (debounce 1,5 с), держит локальную копию в `localStorage` и показывает экран выбора. Слияния по полям нет | CRDT/OT — избыточно для списка из ≤ 200 единиц. Last-write-wins молча теряет правки |
| D3 | **Как game узнаёт о сборниках — проекция из событий.** game слушает `collections.collection.v1` → таблицы `collection_proj` и `collection_items_proj`. Адаптер `CollectionPort` читает их вместе с `catalog_items` (только `active`) и фильтром играбельности | Синхронный вызов game → collections при старте сессии создаёт связность на горячем пути. Чужую БД читать нельзя |
| D4 | **Режим сборника — шаблон конфига, сборник передаётся при старте.** Конфиги `film.collection.frame`, `film.collection.emoji`, `music.collection.intro`, `music.collection.emoji` с source `collection`. `collection_id` и `rounds` приходят в `POST /v1/sessions` (как `subject_ids`) и попадают в `DrawContext.collection_id`. Из `CollectionParams` убирается `collection_id`, добавляются `round_options: [5,10,25,"all"]`, `min_items`, `shuffle`. Сборник хранит `default_mode_key`, game проверяет совпадение домена | Конфиг на каждый сборник — тысячи конфигов. `CollectionSource` в game ещё не регистрировался, поэтому правка параметров ничего не ломает |
| D5 | **Свои ассеты в 3b — только эмодзи** (строгая проверка: только RGI-эмодзи, ≤ 12 графем, без текста). Ассет строится тем же путём, что в ADR 0024: `CollectionPublished.items[].custom.emoji` → media-pipeline собирает набор со `scope = collection:<id>` → `AssetsReady.scope`. Для сессии по сборнику game берёт сначала набор сборника, затем каталожный. **Свои кадры — этап 5** (картинки от пользователей требуют очереди модерации). **Неверные варианты — вместе с `lite` (этап 4).** Своё описание — этап 5 (свободный текст) **[владелец]** | Эмодзи без свободного текста почти не требуют модерации. Отдавать эмодзи из проекции game — та самая вторая модель играбельности, от которой отказались в 3a |
| D6 | **Спойлеры.** Список единиц видят автор, админ и игрок со статусом «пройден» (проверка на сервере). Сборник ищется по названию, описанию и тегам, но не по названиям единиц. Обложка — пресет (градиент и эмодзи из конфига), постеров нет **[владелец]** | Если список единиц открыт всем, игра по сборнику превращается в «прочитай ответы». Поиск по единицам выдаёт состав сборника |
| D7 | **Гости: играют публичные и unlisted сборники, если в режиме `allow_guest`. Создавать сборники, ставить лайки, добавлять в избранное и вести медиатеку гости не могут** (`require_user`, 403 `auth.guest_forbidden`). Гостевые результаты не дают «пройден» и не входят в сложность **[владелец]** | Создание гостем потребовало бы слияния аккаунтов (`AccountsMerged` ещё нет) и открыло бы простой путь для спама |
| D8 | **Минимальная безопасность UGC до этапа 5** (ADR 0030): `gv_text.ugc` — NFKC, удаление управляющих, bidi- и zero-width-символов, схлопывание пробелов, длина считается после нормализации; denylist из файла конфига (ru/en, с учётом похожих символов); в названии нельзя ссылок, `@` и e-mail. Лимиты в `GV_COLLECTIONS__*`: черновиков ≤ 20, публикаций в сутки ≤ 5, единиц 5..200, тегов ≤ 5. Модерация — постфактум: скрытие админом, ограничение автора (локальная таблица collections) и учёт санкции `Sanction.CUSTOM_CONTENT` из claim. Черновики приватны (чужой → 404) | Премодерация каждого сборника без очереди этапа 5 — ручная нагрузка на владельца |
| D9 | **«Пройден» и сложность** — по `game.GameFinished` с `collection_id` (новые необязательные поля). Учитывается первое завершение игрока на версию сборника, только пользователи и не автор. Сложность = 1 − байесовское среднее `score/max_score` (`prior`, `m` и пороги easy/medium/hard в конфиге), пока завершений меньше `min_plays`, показывается `null` | Считать на лету по сессиям — это чужие данные (game) |
| D10 | **Каталог сборников — Postgres** (`tsvector` с конфигурациями `russian` и `simple`, GIN + `pg_trgm` по названию; фильтры по домену, режиму, сложности, кураторским; сортировки `popular` (лайки и прохождения за 7 дней — колонка, пересчитывается периодической задачей), `new`, `likes`, `difficulty`; keyset-курсор) | OpenSearch уже есть у catalog, но для тысяч сборников это лишний индексатор и ещё один путь согласованности. Переход — когда упрёмся, отдельным ADR |
| D11 | **Медиатека: системные полки задаются конфигом** (`watch_later`, `watched`, `favorite`; домены, `title_key`, `exclusive_with`), пользовательские полки (≤ 50, название через `gv_text.ugc`). **Всё приватно**, публичные полки — этап 5. Медиатека проецирует catalog (домен, статус, название, год, персоны), поэтому карточки полки строятся без вызова catalog. Заблокированная единица остаётся на полке с отметкой `unavailable` | Публичные полки — это UGC-текст для всех и вопросы модерации. Обогащать карточки через bff → catalog на каждой странице дороже |
| D12 | **Ручки сохранения:** `PUT/DELETE /v1/shelves/{shelf_ref}/items/{media_item_id}` (естественная идемпотентность + `Idempotency-Key`, CSRF/Origin для cookie), поле `source: reveal/results/card/collection`. Событие публикуется только при фактическом изменении. До ревила `media_item_id` клиенту неизвестен, поэтому кнопка «сохранить» античит не ослабляет | Отдельная ручка на каждый экран дублирует логику |
| D13 | **Лайки и избранное:** `PUT/DELETE /v1/collections/{id}/like|favorite`. Счётчик меняется в той же транзакции. `CollectionLiked` уходит только при новом лайке (`likes_count` в payload), снятие лайка события не публикует. Автор не может лайкнуть свой сборник | Отдельное событие `CollectionUnliked` никому пока не нужно (progression — этап 5) |
| D14 | **Кураторские сборники:** YAML-редактор в админке (паттерн `config_text` из game_config) → admin-API collections `POST/PUT /admin/v1/collections` (`kind=curated`, автор отображается как «Guessverse»). Флаги `featured` и `featured_rank` можно ставить и кураторским, и UGC **[владелец]** | Повторять конструктор в Django — дорого. Кураторство через фронтенд требует прав в публичном API |

## 2. Шаг 0 — оркестратор (contract-first)

**0a** (`stage3b/contracts`) блокирует потоки. **0b** (`stage3b/seed`) — засев и e2e, идёт параллельно.

### 2.1. Реестр, зависимости, CI
- `infra/services.toml`: у `collections` и `library` — `redis = true` (лимиты частоты, как в game), затем `gv gen`. Новых пакетов нет: таблица эмодзи — файл данных в `libs/text`.
- `.github/workflows/ci.yml`: в `e2e-smoke` добавить `e2e/collections.spec.ts`.

### 2.2. События (новые топики)
- `contracts/events/collections/`, топик `collections.collection.v1`, `aggregate_id = collection_id`:
  - `CollectionPublished` и `CollectionUpdated` с одной схемой body: `collection_id`, `version` (монотонная, общая для статусов), `content_version`, `domain`, `kind`, `visibility`, `status (published|hidden)`, `author_id|null`, `default_mode_key`, `items: [{media_item_id, position, custom?: {emoji?}}] ≤ 200`, `published_at`;
  - `CollectionDeleted {collection_id, version, deleted_at}`;
  - `CollectionLiked {collection_id, version, user_id, likes_count, liked_at}`.
- `contracts/events/library/`, топик `library.shelf.v1`, `aggregate_id = user_id`: `ItemSaved` / `ItemRemoved` {`user_id`, `shelf_id`, `shelf_kind: system|custom`, `shelf_key|null`, `media_item_id`, `domain`, `source`, `version`, `at`}.
- `media_pipeline.AssetsReady|AssetsFailed`: необязательное поле `scope?: {kind: "collection", id}`.
- Примеры в `examples/` для всех событий, в том числе consumed-пример `GameFinished` с `collection_id`.

### 2.3. `game.GameFinished` (расширение v1 и исправление дефекта 3a)
- **Дефект:** `contracts/events/game/GameFinished.v1.json` ограничивает `rounds.maxItems = 50` и `index.maximum = 49`. При этом `session.rounds ≤ 200`, а сиды `film|music.marathon.*` объявляют `rounds: 200`. Марафон с пулом больше 50 единиц публикует событие, не проходящее схему. В e2e это не проявилось: в синтетике 40 единиц.
- **Решение:** расширить до 200/199 в v1. Потребителей `game.session.v1` пока нет (проверено grep по `services/*/src`), collections станет первым. Согласовать в 0a как допустимое расширение. Регресс-тест в B: марафон на 60 раундов → `assert_event_matches_schema`.
- Новые необязательные поля: `collection_id?`, `collection_version?`.

### 2.4. `contracts/modes/`
- Новая схема `params/source.collection.v1.json`: `round_options` (подмножество `{5,10,25,"all"}`, уникальные), `min_items` (≥ 1), `shuffle`.

### 2.5. API (0a: схемы, заглушки 501, снимок OpenAPI)
- **collections, public** (`/api/collections`):
  - `POST /v1/collections` — `{domain, title}` → черновик;
  - `GET /v1/me/collections?status=`;
  - `GET|PUT /v1/collections/{id}/draft` (D2);
  - `POST /v1/collections/{id}/publish {draft_revision}`;
  - `DELETE /v1/collections/{id}`;
  - `GET /v1/collections?q=&domain=&mode=&difficulty=&curated=&sort=&cursor=&limit≤50`;
  - `GET /v1/collections/{id}` — карточка и `me: {liked, favorite, completed}`; `items` только по D6;
  - `PUT|DELETE /v1/collections/{id}/like|favorite`;
  - `GET /v1/me/favorites`;
  - `GET /v1/meta` — лимиты, пресеты обложек, теги.
- **collections, admin:**
  - `GET /admin/v1/collections?q=&status=&kind=`, `GET /admin/v1/collections/{id}`;
  - `POST|PUT /admin/v1/collections` (кураторские, D14);
  - `POST .../{id}/hide|unhide {reason}`, `PUT .../{id}/featured {featured, rank, reason}`;
  - `POST|DELETE /admin/v1/authors/{user_id}/restriction {reason}`.
  - Все мутации — с `Idempotency-Key`.
- **library, public** (`/api/library`):
  - `GET /v1/shelves`, `POST /v1/shelves {title}`, `PATCH|DELETE /v1/shelves/{id}`;
  - `GET /v1/shelves/{ref}/items?cursor=` (`ref` — `system:<key>` или uuid);
  - `PUT|DELETE /v1/shelves/{ref}/items/{media_item_id} {source}`;
  - `GET /v1/items/{media_item_id}/shelves` — на каких полках единица (для кнопки).
- **game, public:**
  - `POST /v1/sessions` — плюс `collection_id?`, `rounds?`;
  - `GET /v1/collections/{id}/modes` → `[{mode_key, playable, total, round_options}]` (только опубликованный, не скрытый сборник);
  - `POST /v1/modes/{mode_key}/playability {media_item_ids ≤ 200}` → `{playable_ids}` — конструктору для черновика, с лимитом;
  - в `result` — `collection?: {id, version}`.
- **game, admin (хвост 3a):** `GET /admin/v1/mode-configs/{mode_key}/subjects?q=` (`game.daily.view`) — роли и `min_items` берутся из конфига сетки.
- **media_pipeline, admin (хвост 3a):** `GET /admin/v1/sources?media_item_id=` → `[{source_media_id, kind, position, origin, created_at}]`.
- **bff_web:**
  - `GET /v1/pages/collections` — блоки `featured`, `popular`, `new` либо `results` при `q`;
  - `GET /v1/pages/collections/{id}` — карточка, `modes` из game, `me`;
  - `GET /v1/pages/library`, `GET /v1/pages/library/{ref}`.

### 2.6. Ядро — сигнатуры (0a, `libs/game_kernel`)
- `plugins.py`: `DrawContext.collection_id: UUID | None`.
- `ports.py`: `CollectionPort.items(collection_id, limit, *, playable_for: PlayableFor | None)`, `PlayableFor.scope`.
- `builtin/sources.py`: `CollectionParams` без `collection_id`, с `round_options` и `min_items`; `CollectionSource.rounds_for(params, requested, pool) -> int` — только сигнатура.

### 2.7. `libs/text`
- `gv_text/ugc.py`: `normalize_ugc(text, max_len) -> str`, `UgcPolicy` (denylist из файла, проверка похожих символов, запрет ссылок и упоминаний), ошибки с кодом.
- `gv_text/emoji.py`: `graphemes()` (перенос из catalog, тот же алгоритм и golden-векторы) и `is_strict_emoji()` по таблице `data/emoji-sequences.txt` (Unicode 16, файл данных с лицензией Unicode).
- Тесты: golden для bidi, zero-width, NFKC, похожих символов, флагов, ZWJ-последовательностей и тонов кожи.

### 2.8. Права и настройки
- identity `0007_seed_collections_perms.py` (паттерн `0005`, только вставка):
  - `collections.view`, `collections.curate`, `collections.unpublish`, `collections.restrict_author`;
  - admin получает все права, moderator — `view` и `unpublish`.
- `env.example.j2`:
  - `GV_COLLECTIONS__LIMITS__*` (D8);
  - `GV_COLLECTIONS__DIFFICULTY__{PRIOR,M,MIN_PLAYS,THRESHOLDS}`;
  - `GV_UGC__DENYLIST_PATH`;
  - `GV_LIBRARY__SHELVES_CONFIG`, `GV_LIBRARY__LIMITS__*`;
  - `GV_GAME_LIMITS__PLAYABILITY_PER_MIN = 30`.

### 2.9. ADR и документация
- 0028 «Сборники: версии, черновик с оптимистичной блокировкой, проекция в game, сборник в параметрах старта» (D1–D4, D6, D9–D10; уточняет 0008, 02-game-engine §5).
- 0029 «Ассеты сборников — наборы со `scope`» (D5; уточняет 0020 и 0024).
- 0030 «Минимальная безопасность UGC до модерации этапа 5» (D7–D8, D13–D14).
- 0031 «Медиатека: системные полки из конфига, приватность, проекция каталога» (D11–D12).
- Обновить: `architecture/services.md` §7–8 (данные, события 3b), `02-game-engine.md` §5.1, `architecture/security.md` §3 (UGC), `CLAUDE.md` сервисов.

## 3. Потоки и владение файлами

| Поток | Ветка | Может менять |
|---|---|---|
| **A** ядро | `stage3b/kernel` | `libs/game_kernel/**` (кроме сигнатур 0a), `libs/testkit/src/gv_testkit/game.py` |
| **K** collections | `stage3b/collections` | `services/collections/**`, `contracts/openapi/collections.json` |
| **L** library | `stage3b/library` | `services/library/**`, `contracts/openapi/library.json` |
| **B** game | `stage3b/game` | `services/game/**`, `contracts/openapi/game.json`, `seeds/game/**` |
| **M** media | `stage3b/media` | `services/media_pipeline/**`, `contracts/openapi/media_pipeline.json` |
| **C** bff | `stage3b/bff` | `services/bff_web/**`, `contracts/openapi/bff_web.json` |
| **D** фронтенд | `stage3b/web` | `frontend/**` |
| **E** admin | `stage3b/admin` | `services/admin/**` |
| Оркестратор | `stage3b/contracts`, `stage3b/seed` | `contracts/{events,modes}/**`, `libs/{text,core,service,testkit}` (кроме `game.py`), `services/identity/**/0007_*`, `tools/gv/**`, `seeds/{collections,catalog}/**`, `e2e/**`, `.github/**`, `infra/**` (через `gv gen`), `docs/**` |

## 4. Поток A — ядро

| # | Файлы (`libs/game_kernel/src/gv_game_kernel/...`) | Действие | Тест | Риск |
|---|---|---|---|---|
| A1 | `builtin/sources.py` | `CollectionSource.draw`: `ctx.collection_id` обязателен (иначе `ValueError` → в game 422); единицы через `items(..., playable_for)`; `rounds_for`: `"all"` → весь пул, иначе запрошенное число, если оно есть в `round_options` и ≤ пула; пул < `min_items` → `PoolTooSmall` | unit: детерминизм при одном seed, `shuffle=false` сохраняет порядок, `all`, повторы и `exclude_ids` | низкий |
| A2 | `registry.py` | `validate`: у `collection` только `classic`; `round_options` ≤ `session.rounds`; `features.records` запрещён для `collection` (ошибка с путём) | «сломанный конфиг на каждое правило» | низкий |
| A3 | `engine.py` | `draw_rounds(ctx, n)` получает `n` из `rounds_for`; `max_score(n)` — по фактическим раундам | property: `max_score` = сумма по выданным раундам | низкий |
| A4 | `gv_testkit/game.py` | `FakeCollectionPort` (порядок, `playable_for`, `scope`), DSL `.given_collection(...)` | — | низкий |

**Готово, когда** `gv test libs/game_kernel` зелёный (≥ 90 %), а сиды 3a проходят без изменений.

## 5. Поток K — collections (основной объём)

| # | Файлы (`services/collections/src/collections_service/...`) | Действие | Тест | Риск |
|---|---|---|---|---|
| K1 | `infrastructure/db/migrations/versions/0002_collections_core.py`, `db/tables.py` | `collections` (CHECK согласованности статуса и `kind`; `title_tsv` GIN; trgm по названию; `likes_count`, `plays_count`, `popular_score`, `difficulty`, `featured_rank`), `collection_versions`, `collection_drafts`, `likes`, `favorites` (PK `(user_id, collection_id)`), `completions (user_id, collection_id, version, ratio, first_at)`, `play_stats`, `author_restrictions`, проекции `catalog_items_proj`, `users_proj (user_id, nickname)`, `idempotency_keys` | integration: миграция вверх и вниз, CHECK-и отвергают битые строки | средний |
| K2 | `domain/collection.py`, `domain/draft.py` | агрегат: `create`, `save_draft(base_revision)` → `DraftConflict(current)`, `publish` (5..200 единиц, домен единиц = домен сборника, без повторов, `default_mode_key` по шаблону `^{domain}\.collection\.[a-z0-9_]+$`), `hide/unhide`, `delete`, `remove_item(media_item_id)` → новая версия; события | unit: инварианты, конфликт, правки скрытого сборника → 409 `collections.hidden`, публикация удалённого → 409 | средний |
| K3 | `domain/policy.py` | обёртка над `gv_text.ugc` для названия (≤ 80), описания (≤ 500) и тегов (из словаря `meta`); `custom.emoji` — `is_strict_emoji`, ≤ 12 графем; пресет обложки из конфига | golden-тесты отказов с кодами | средний |
| K4 | `domain/difficulty.py` | D9: формула и корзины из настроек | property: монотонность, `null` при `< min_plays` | низкий |
| K5 | `application/commands/{drafts,publish,reactions}.py`, `infrastructure/limits.py` | use-cases с UoW, outbox и `Idempotency-Key`; лимиты: Redis (fail-closed для публикации, fail-open с метрикой для лайков) + счётчики в БД (черновики); проверки: `require_user`, `Sanction.CUSTOM_CONTENT`, `author_restrictions`, единицы активны в проекции catalog | component: публикация → событие валидно по схеме; повтор ключа — тот же ответ; чужой черновик → 404; ограниченный автор → 403 `collections.author_restricted`; гость → 403 | **высокий** — центр UGC-безопасности |
| K6 | `application/queries/{catalog,collection}.py`, `infrastructure/search.py` | поиск по D10: `websearch_to_tsquery` + trgm, keyset-курсор `(sort_key, id)`, только `published` + `public`; карточка с `items` по D6 и `me` | integration: порядок сортировок, курсор без дублей; EXPLAIN на 10 тыс. строк использует индексы | средний |
| K7 | `infrastructure/messaging/__init__.py`, `application/commands/projection.py` | consumers (inbox, проверка `version`): `catalog.MediaItem*` (проекция; `Blocked` → `remove_item` во всех сборниках порциями, затем `CollectionUpdated`); `game.GameFinished` (`collection_id` есть, пользователь не автор → `completions`, `play_stats`, `difficulty`); `identity.UserRegistered/ProfileBasicsChanged` (ник); `identity.UserDeleted` (вопрос 5) | contract: consumed-примеры; повтор — no-op; устаревшая `version` отброшена | средний |
| K8 | `infrastructure/jobs.py`, `main.py` | `collections.popular.recompute` (окно 7 дней, порции), очистка `idempotency_keys` — `PeriodicJob` в relay | `run_periodic_job` | низкий |
| K9 | `api/public`, `api/admin` | роутеры §2.5; admin: `admin.access` + право, `reason`, `Idempotency-Key`; mass assignment: `kind`, `author_id`, `featured`, `status` из публичного API не принимаются (`extra=forbid`) | component на каждую ручку, 42crunch-audit | средний |

**Готово, когда** `gv test collections` зелёный (domain ≥ 90 %, application ≥ 85 %), а проверки §12 п. 1–4 и 6 закрыты.

## 6. Поток L — library

| # | Файлы (`services/library/src/library_service/...`) | Действие | Тест | Риск |
|---|---|---|---|---|
| L1 | `migrations/versions/0002_library_core.py`, `db/tables.py` | `shelves` (`user_id`, `kind system/custom`, `key|null`, `title|null`, UNIQUE `(user_id, key)`), `shelf_items` (PK `(shelf_id, media_item_id)`, `source`, `added_at`, индекс `(user_id, media_item_id)`), `catalog_items_proj`, `idempotency_keys`, `user_versions` | integration: миграция | низкий |
| L2 | `domain/shelf.py`, `config/shelves.yaml` | системные полки создаются лениво при первом обращении; `exclusive_with` (`watched` ↔ `watch_later`); домен единицы ∈ доменов полки; лимиты | unit: исключение, лимиты, повторное сохранение — no-op без события | низкий |
| L3 | `application/commands/save.py` | `SaveItem`/`RemoveItem`: UoW, outbox `ItemSaved`/`ItemRemoved` только при изменении, `Idempotency-Key`, CSRF/Origin | component: сохранение из `reveal` → событие валидно; гость → 403; неизвестная или заблокированная единица → 422 `library.item_unavailable` | средний |
| L4 | `infrastructure/messaging/__init__.py` | `catalog.MediaItem*` → проекция (блокировка ставит отметку, не удаляет); `identity.UserDeleted` → удалить все полки и элементы пользователя (порциями, идемпотентно) | contract: consumed-примеры | низкий |
| L5 | `api/public` | ручки §2.5; `shelf_ref` разбирается строго; чужая полка → 404 | component, 42crunch-audit | низкий |

## 7. Поток B — game

| # | Файлы (`services/game/src/game_service/...`) | Действие | Тест | Риск |
|---|---|---|---|---|
| B1 | `infrastructure/db/migrations/versions/0005_collections.py`, `db/tables.py` | `collection_proj (collection_id, version, domain, status, visibility, author_id)`, `collection_items_proj (collection_id, position, media_item_id)`; в `clue_assets` — `scope_id uuid null`, уникальность `(media_item_id, clue_type, params_hash, scope_id) NULLS NOT DISTINCT` (новый индекс создаётся `CONCURRENTLY`, старый удаляется в следующем релизе) | integration: вверх и вниз на данных 3a | средний |
| B2 | `domain/projection.py`, `application/commands/projection.py`, `infrastructure/messaging/__init__.py` | consumers `collections.collection.v1` (замена состава по `version`, `Deleted` удаляет проекцию и scoped-ассеты); `AssetsReady.scope` → `scope_id` | contract: consumed-примеры; перестановка событий | низкий |
| B3 | `infrastructure/plugins.py` (адаптер `CollectionPort`), `application/rules.py` | items ∩ `catalog_items.active` ∩ играбельность: набор `scope` сборника, иначе каталожный (ревил — тоже по `reveal_playable_for`) | integration: совпадение с `FakeCollectionPort` | средний |
| B4 | `application/commands/play.py`, `domain/session.py`, `domain/events.py` | старт: сборник `published` (скрытый или удалённый → 404 `game.collection_not_found`), домен совпадает с режимом, `rounds` через `rounds_for`; в state — `collection_id` и `collection_version`; `GameFinished` с новыми полями; ассет раунда — scoped | component: игра по сборнику, свои эмодзи, гость при `allow_guest`, скрытие во время игры (сессия доигрывается), **регресс дефекта §2.3**: марафон на 60 раундов → событие валидно | **высокий** |
| B5 | `application/queries/play.py` | `CollectionModes` (опубликованные режимы `source=collection` домена, `playable/total`); `Playability` (≤ 200 id, лимит, без названий в ответе) | component: число играбельных совпадает со стартом | низкий |
| B6 | `api/admin/__init__.py`, `application/queries/play.py` | **хвост 3a:** `subjects` для сетки (переиспользует `ListSubjects`) | component: права, лимит 20 | низкий |
| B7 | `seeds/game/modes/{film.collection.frame,film.collection.emoji,music.collection.intro,music.collection.emoji}.yaml` | сиды D4 (`records` выключен) | валидатор ядра в CI | низкий |

## 8. Поток M — media-pipeline

| # | Файлы (`services/media_pipeline/src/media_pipeline_service/...`) | Действие | Тест | Риск |
|---|---|---|---|---|
| M1 | `migrations/versions/0007_scoped_assets.py`, `domain/asset_set.py`, `domain/sources.py` | `scope_kind`, `scope_id` у `source_media` (вид `text`) и `asset_sets`; уникальность с учётом scope | integration: миграция | средний |
| M2 | `application/commands/collections.py` (новый), `infrastructure/messaging/__init__.py` | consumer `collections.collection.v1`: для `custom.emoji` — источник `text` со scope и хэшем содержимого, затем `PlanAssetSets` только для профилей `emoji` (статических и динамических); изменение → новое поколение; удаление эмодзи, `Deleted` или скрытие → наборы со scope удаляются, объекты S3 чистит существующая очистка; `AssetsReady/Failed.scope` | component: публикация → `AssetsReady` со scope; повтор — no-op; эмодзи вне строгого набора → `PermanentError` (защита в глубину) | средний |
| M3 | `api/admin/__init__.py` | **хвост 3a:** `GET /admin/v1/sources?media_item_id=` (`admin.access`) | component | низкий |

## 9. Поток C — bff-web
- `application/queries/{collections,library}.py`, `infrastructure/clients.py`: клиенты `collections` и `library` (`GV_UPSTREAMS__{COLLECTIONS,LIBRARY}_URL`), страницы §2.5. Деградация по блокам: если game недоступен, блок `modes` получает `unavailable`, карточка всё равно отдаётся. Публичный каталог кэшируется (`public, max-age=30`, без `q`), личное — `no-store`.
- `PageLayout`: `collection_source_types` не попадают в витрину `/solo`.
- Тесты: component на `stub_service`; гость получает `me: null`; при недоступном collections — 200 с `unavailable`. Риск низкий.

## 10. Поток D — фронтенд

| # | Файлы (`frontend/src/...`) | Действие | Тест |
|---|---|---|---|
| D1 | `shared/api/client.ts`, `shared/api/schema/*` (`pnpm gen:api`) | префиксы `/api/collections`, `/api/library` | typecheck |
| D2 | `features/collections/editor/*`, `app/collections/new/page.tsx`, `app/collections/[id]/edit/page.tsx` | конструктор: домен, поиск единиц (catalog autocomplete), порядок перетаскиванием и с клавиатуры, отметка «играбельно» (`playability`), эмодзи с проверкой на клиенте, пресет обложки; автосохранение и экран конфликта (D2) | vitest + MSW: 409 → выбор версии; редьюсер черновика |
| D3 | `features/collections/catalog/*`, `app/collections/page.tsx`, `app/collections/[id]/page.tsx` | каталог: поиск (debounce), фильтры, сортировки, бесконечная лента по курсору; страница: режимы и выбор числа раундов, лайк и избранное (оптимистично, откат при ошибке), «пройден», список единиц, если он доступен | Playwright на моках, axe |
| D4 | `app/me/collections/page.tsx` | мои черновики и опубликованные, плашка «скрыт модератором» | vitest |
| D5 | `features/library/{SaveButton,ShelfPicker}.tsx`, `features/game/RevealCard.tsx`, `features/results/ResultsView.tsx` | «Сохранить → куда» в ревиле, итоге и карточке; для гостя — ссылка на регистрацию | MSW: `exclusive_with` отражается сразу |
| D6 | `app/library/page.tsx`, `app/library/[ref]/page.tsx` | полки, своя полка (создание, переименование, удаление), `unavailable` | Playwright |
| D7 | `app/play/[modeKey]/page.tsx`, `features/game/StartGame.tsx`, `features/results/share.ts` | старт с `collection` и `rounds`; в шэре — название сборника и ссылка | unit шэра |
| D8 | `e2e/{collections,library,draft-conflict}.spec.ts`, `e2e/mocks/` | моки по контрактам | — |

Пользовательский текст выводится только через JSX-экранирование, без `dangerouslySetInnerHTML`, для bidi — `dir="auto"`.

## 11. Поток E — admin
- `apps/collections/`:
  - список и поиск (`collections.view`);
  - карточка с составом;
  - «Скрыть/Показать» (`collections.unpublish`);
  - «Рекомендуемое» с порядком;
  - «Ограничить автора» (`collections.restrict_author`);
  - YAML-редактор кураторского сборника (`collections.curate`, переиспользует `config_text`; единицы задаются по `media_item_id` и проверяются сервисом).
- `core/collections_client.py` (новый). `apps/game_daily/`: combobox темы на `subjects` (хвост). `apps/catalog/`: таблица исходников с позициями (хвост; перестановка — существующий `PUT position`).
- Тесты: pytest-django на `stub_service`; без права — 403 и скрытый пункт меню; аудит пишется и при отказе; в аудит попадают ревизии и статусы, не тексты.

## 12. Обязательные проверки безопасности
1. **IDOR/BOLA:** чужой черновик, полка, неопубликованный сборник → 404; `items` сборника — только по D6 (проверка на сервере, тест на каждого субъекта: аноним, гость, игрок, прошедший игрок, автор, админ).
2. **Mass assignment:** публичные схемы `extra=forbid`; `kind`, `author_id`, `featured`, `status` задаются только через admin-API.
3. **UGC-текст:** нормализация до проверки длины и denylist; запрет ссылок в названии; строгие эмодзи на обеих сторонах (collections и media); экранирование на фронте.
4. **Лимиты:** публикация — fail-closed; черновики — счётчиком в БД; лайки, избранное, `playability`, сохранение — Redis fail-open с метрикой.
5. **Античит:** снимок сессии по сборнику не содержит `media_item_id` (рекурсивный обход, как в 3a); scoped-ассет выдаётся только для открытого уровня; `playability` не возвращает названий; самоигра автора не входит в сложность.
6. **Целостность:** `Idempotency-Key` у всех мутаций; события публикуются в транзакции мутации; consumers идемпотентны по inbox и `version`.
7. **CSRF/Origin** для cookie-аутентификации в collections и library (`libs/auth`).
8. **identity:** только миграция-справочник прав (`0007`) — ревью уровня §3 `10-ai-assistant.md`, без изменений логики выдачи токенов.
9. **Процедура:** K, L, B, M, E — `security-discipline`; новые OpenAPI — `42crunch-audit`; K7, M2 — `idempotency-and-side-effects`; миграции — `review-migration`; запросы K6 — `explain-this-query`.

## 13. Миграции и совместимость
- Все миграции только расширяющие: collections `0002`, library `0002`, game `0005`, media `0007`, identity `0007`. Индекс `clue_assets` создаётся в два шага (новый `CONCURRENTLY`, старый удаляется в следующем релизе).
- События: новые поля `GameFinished`, `AssetsReady/Failed` необязательны. Порядок выкладки: сначала game и media (готовы принять `scope` и новые топики), потом collections (продюсер), затем library.
- `CollectionParams` меняется до первой регистрации `collection` в game, поэтому сохранённых конфигов с `collection_id` нет (проверка в A2).
- Расширение `GameFinished.rounds` до 200 согласуется в 0a (потребителей нет).

## 14. Что НЕ входит
- Свои кадры и описания в сборниках, очередь модерации, жалобы, комментарии к сборникам — этап 5.
- Формат `lite` и неверные варианты — этап 4. Игра по сборнику в онлайне и кастомное лобби — этап 4.
- Публичные полки и подписки на авторов — этап 5. Экспорт и синхронизация полок, `ShelfExportRequested` — этап 6. Импорт сборника по ссылке (Яндекс, Кинопоиск) — этап 6.
- Смешанные сборники (музыка + кино) — открытый вопрос концепции §10.3, не в 3b.
- Награды за публикацию и прохождение — этап 5. Постеры в медиатеке и каталоге сборников — после появления публичного профиля постеров.
- **Серверный `share_text` по `presentation`** (хвост 3a): переносить не нужно. Клиентский реестр шэра работает, а серверный текст понадобится вместе с OG-карточками на этапе 7 (там же i18n на сервере).

## 15. Риски

| Риск | Мера |
|---|---|
| Спам и оскорбительные сборники до модерации этапа 5 | D8: лимиты, нормализация и denylist, emoji-only кастом, скрытие и ограничение автора, санкция из claim |
| Спойлеры через страницу, поиск или `playability` | D6, ответы без названий, тест «аноним не видит состав» |
| `MediaItemBlocked` по популярной единице меняет тысячи сборников | Порции, по одной версии на сборник, метрика длины очереди |
| Горячая строка `likes_count` | Масштаб этапа приемлем; позже — счётчики в Redis (отметить в ADR 0030) |
| Индекс `clue_assets` со scope ломает поиск ассетов дейлика | Два шага миграции, регресс component-тестов дейлика и случайного режима |
| Расширение `GameFinished` до 200 раундов | Согласовать в 0a; регресс-тест марафона |
| Объём этапа (два новых сервиса) | Отдельные потоки K и L. Свои эмодзи (K3 эмодзи-часть, M2, B3 scope) можно вынести без влияния на остальное |

## 16. Порядок слияния
1. **0a** `stage3b/contracts` (контракты, `libs/text`, `services.toml`, identity `0007`, ADR); от неё ветвятся все потоки.
2. **A** (ядро), затем rebase B.
3. **M** и **B** — потребители новых топиков и `scope` — до продюсера.
4. **K** (collections), затем **L** (library).
5. **0b** — засев: `seeds/collections/*.yaml` через admin-API, `tools/gv/src/gv_cli/seed.py` (ожидание `AssetsReady` со scope), `e2e/collections.spec.ts` (сценарий §0 п. 2–7, две вкладки для конфликта).
6. **C**, затем **E**.
7. **D** последним: e2e на полном стеке, живая демонстрация §0.

После каждого слияния: `uv lock --check`, `gv gen --check`, `gv contracts export && gv contracts check`, `gv test --changed`, `gv test frontend`. В конце — обновить `09-roadmap.md` (итог 3b и хвосты), принять ADR 0028–0031.

## Решения владельца (2026-10-16)
1. D5: свои ассеты в 3b — только эмодзи (строгая проверка); свои кадры и описания — этап 5.
2. D7: гости только играют публичные/unlisted сборники; создание, лайки, избранное и медиатека — только пользователям.
3. D6: состав сборника видят автор, админ и прошедший игрок; поиск — по названию, описанию и тегам.
4. D8: модерация постфактум (лимиты, нормализация и denylist, скрытие и ограничение автора в админке).
5. `UserDeleted`: черновики удаляются, опубликованные сборники обезличиваются («автор удалён») и остаются в каталоге.
6. D14: кураторские сборники — YAML-редактор в админке + флаг «рекомендуемое» для любых сборников; хвосты 3a (поиск тем сетки, список исходников) закрываются в 3b, серверный `share_text` — этап 7.
