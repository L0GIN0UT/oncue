# План этапа 2: Соло-игра

> Статус: **утверждён** владельцем 2026-10-10: D5 (гости играют дейлик и случайный), D6 (гибрид, смена дня 00:00 Europe/Moscow), D3 (Tailwind + shadcn/ui) выбраны явно, остальные решения — по рекомендациям. Основа: [roadmap](../09-roadmap.md) (этап 2 и «Хвосты этапа 1»), [игровой движок](../02-game-engine.md), [план этапа 1](stage-1.md).
> После утверждения решения фиксируются в ADR 0016–0020 (§2.9).

## 0. Цель и критерии готовности

**Цель.** В браузере можно пройти дейлик в музыке (`audio_intro`, 5 треков) и «Кадр дня» в кино (`frame_progressive`, 5 фильмов), увидеть ревил после каждого раунда и итог, сыграть случайный режим с фильтром. Правила, уровни, очки и пул контента задаются версионируемым `ModeConfig`, который админ правит и публикует в админке. Ответ проверяет только сервер; клиент получает ассет уровня только после того, как уровень открыт.

**Демонстрация (этап считается сделанным, когда всё это проходит):**
1. Стек поднят (`gv dev up infra`, `gv dev up services`, `frontend`). Каталог импортирован из `seeds/catalog`, исходники ассетов загружены из `seeds/media` → `AssetsReady` → проекция game заполнена. Game опубликовал сиды режимов `music.daily.classic`, `film.daily.frame`, `music.random.classic`, `film.random.frame`, периодическая задача создала наборы дня на сегодня и N дней вперёд.
2. Гость открывает `https://localhost/`, видит две карточки дейликов и обратный отсчёт до смены дня. «Музыка»: уровень 1 — 0,5 с. Неверный ответ → уровень 2, «Пропустить (+N с)» → уровень 3, выбор из автокомплита → верно → ревил (название, исполнитель, отрывок 15 с, очки). После 5 раундов — итог 0..30, строки по раундам, «Поделиться» копирует текст-сетку, видна доля угадавших по каждому раунду. Повторный вход в дейлик открывает результат, переиграть нельзя.
3. В DevTools: снимок сессии и ответы на ход не содержат `media_item_id` и названия до ревила. Ссылка ассета — `/media/a/<hex>.<ext>` конкретного уровня. Запрос ассета уровня выше текущего → 403 `game.level_locked`. Через 60 с ссылка отдаёт 403 (nginx).
4. «Кадр дня»: с каждой попыткой кадр чётче; ревил — чёткий кадр, год, режиссёр.
5. Регистрация → вход → дейлик. В Redpanda — `game.GameFinished` и `game.DailyCompleted`, оба валидны по схеме. У гостя — только `GameFinished` с `is_guest = true`. Случайный режим (музыка, пресет «2000-е») стартует, его можно пройти.
6. Админ в `admin` правит черновик `music.random.classic` (очки уровней). Ошибка схемы видна в форме с путём к полю. Публикация v2 → `game.ModeConfigPublished`. Новая случайная сессия играет на v2, сегодняшний дейлик — на версии, с которой создан его набор. Замена единицы в завтрашнем наборе пишет аудит.
7. `POST media_pipeline /internal/v1/asset-links` без сервисного токена → 401, с пользовательским JWT → 401, с токеном `svc:game` → 200.
8. `gv lint`, `gv test --changed` (все уровни), `gv contracts check`, `gv gen --check`, `nginx -t`; во `frontend/` — `pnpm lint && pnpm typecheck && pnpm test && pnpm build` и Playwright на моках. В CI на `main` — e2e «дейлик» на compose.

## 1. Сквозные решения (утверждены)

| # | Решение (рекомендация) | Варианты и почему |
|---|---|---|
| D1 | **Фронтенд:** Next.js (App Router, актуальная стабильная major на момент старта, фиксируется в lock), React 19, TypeScript `strict`, pnpm, Node 24 LTS. Каталог **`frontend/`** — одно приложение без JS-workspace. Сборка `output: "standalone"`, контейнер `web` за gateway (`location /`) | Стек и `frontend/` уже заданы в [architecture/README](../architecture/README.md) и [08-development-workflow](../08-development-workflow.md). `apps/web` + pnpm workspace — когда появится второй клиент (Telegram Mini App), переезд механический. Vite SPA проще, но закрывает SSR и SEO этапа 7 |
| D2 | **Рендеринг:** SSR (server components) — каркас, главная и хаб: публичные данные берутся у bff по внутреннему адресу. **CSR** — личный статус (дейлики пользователя) и весь игровой экран (TanStack Query, `no-store`) | Полный SSR требует обновлять токен на сервере Next: refresh-cookie живёт на пути identity, а повторное использование refresh отзывает сессию (`services/identity/CLAUDE.md`). Полный CSR даёт медленную первую отрисовку и плохо подходит для SEO в будущем |
| D3 | **UI-кит:** Tailwind CSS v4 + shadcn/ui (компоненты Radix копируются в репозиторий) + lucide. Токены дизайна — CSS-переменные, тёмная тема по умолчанию. Тексты — ключи next-intl (пока только `ru`). Визуальное направление — скилл `frontend-design` | MUI и Mantine тяжелее и выглядят «по умолчанию». Чистый CSS Modules — дольше без выигрыша. shadcn не тянет runtime-зависимость: код компонентов лежит у нас |
| D4 | **Куда ходит браузер:** команды игры (старт, ход, пропуск, ассет) — **напрямую в game** (`/api/game/v1`, cookie + CSRF из `gv_auth`). Автокомплит — напрямую в catalog (как в [services.md](../architecture/services.md)). **bff-web — только чтение** экранов (GET-агрегаты) и источник для SSR | Всё через bff — лишний хоп на ходе (SLO p95 < 150 мс), дублирование контрактов, CSRF и идемпотентность пришлось бы делать в bff. Без мутаций bff остаётся stateless-агрегатором без БД |
| D5 | **Гости играют** в дейлики и случайный режим (`session.allow_guest` в конфиге режима). Гостевая сессия выдаётся **identity**: `POST /v1/auth/guest` → access JWT `sub = guest uuid7`, `roles = ["guest"]`, `gst = true`, без прав, refresh как у обычной сессии. Результаты гостя хранятся, но не идут в статистику дня и в `DailyCompleted`. Перенос результатов в аккаунт — этап 5 | «Только с аккаунтом» — проще, но хуже конверсия, а Songless пускает гостей. Подписанная cookie гостя в game — второй механизм аутентификации рядом с JWT. Через identity у всех сервисов один `Principal` |
| D6 | **Контент дейлика — гибрид:** Source `daily` генерирует набор по правилам пула из конфига (`playable` под параметры загадки, `min_popularity`, жанры, `exclude_recent_days`, секретный seed). Периодическая задача создаёт наборы на `schedule_ahead_days` вперёд, набор фиксирует `mode_version`. Админ видит будущие наборы и **заменяет единицы до начала дня**, в начале дня набор блокируется. Смена дня — 00:00 в `source.params.timezone` (по умолчанию `Europe/Moscow`) | Только ручная курация — ежедневная работа модератора и риск пустого дня. Только автогенерация — нельзя убрать кадр с надписью-подсказкой (риск этапа 1). Генерация на лету в 00:00 даёт всплеск нагрузки ([scalability §3](../architecture/scalability-resilience.md)) |
| D7 | **Связь ModeConfig ↔ ассеты по `params_hash`:** `clue.params` в конфиге **совпадает** с параметрами профиля media-pipeline. game находит ассеты по `(media_item_id, clue_type, params_hash)` из `AssetsReady`. **Очки уровней переезжают** из `clue.levels[].points` в `scoring.params.points`: иначе хэш не совпадёт. При публикации проверяется, что пул играбельных единиц не меньше минимума. Генерация ассетов под новый конфиг (`ClueAssetsRequested`) — этап 3 | Ссылка по `profile_key` проще, но game не знает параметров уровней (длительности для UI) и не замечает, что профиль изменился. `params_hash` уже в событии; его алгоритм фиксирует golden-файл. Отступление от [02-game-engine §4](../02-game-engine.md) — нужен ADR |
| D8 | **Конфиги режимов** хранит game (Postgres): неизменяемые опубликованные версии `(key, version)` и один черновик на ключ с `revision`. Контракт — JSON Schema в `contracts/modes/` (пишется руками, модели ядра сверяются contract-тестом). **Редактор в админке на этапе 2** — JSON/YAML-поле + серверная валидация (ошибки с путём) + diff с опубликованной версией + публикация, откат и архив. Форма по JSON Schema — этап 5. Сиды режимов в local/ci публикуются при старте game из `GV_GAME__BOOTSTRAP_MODES_DIR` | Генерированные формы для полиморфных `params` (oneOf по `type`) неудобны и дороги, а валидация всё равно серверная. Хранить конфиги в файлах репозитория — каждая правка становится релизом (против [ADR 0008](../adr/0008-plugin-game-engine.md)) |
| D9 | **Попытки как в Songless:** число уровней загадки = число попыток, на уровень один ответ (`session.attempts_per_level = 1`). «Пропустить» открывает следующий уровень (`session.skip_allowed`), «Сдаться» завершает раунд. Ответ — выбранная в автокомплите единица (`media_item_id`) или свободный текст (matcher). Всё задаётся конфигом, match переиспользует те же правила с `attempts_per_level = 3` | Счётчик попыток отдельно от уровней — лишняя ось без продуктовой нужды. Только свободный текст плохо работает с опечатками и дублями названий |
| D10 | **Очки:** `by_level` для дейликов (`points: [6,5,4,3,2,1]`, частичный ответ «угадан исполнитель» × `partial_ratio = 0.5`). `by_speed` (сервер считает время от выдачи первого ассета раунда) реализуется и покрывается тестами, выбирается конфигом (сид `music.random.speed` — по желанию). Максимум очков считает Scorer, а не клиент | Оба варианта требует roadmap, они взаимозаменяемы через реестр |
| D11 | **Античит:** ответ проверяет только сервер. В снимке сессии до ревила нет `media_item_id`, названия и `asset_set_id`. Ссылку на ассет выдают только для уровня ≤ текущего (`subject = sess:<session_id>`, TTL 60 с, [ADR 0012](../adr/0012-signed-asset-links-nginx.md)). Ревил — только для завершённого раунда. В каждом ходе `expected_version`, на ходах `Idempotency-Key`, rate-limit, `Cache-Control: no-store`. Принятый риск: гость может переиграть дейлик, сбросив cookie (результаты гостей не входят в статистику) | Отдавать все уровни заранее зашифрованными — ключ всё равно окажется на клиенте |
| D12 | **Соло-сессии — в Postgres** (источник истины, outbox в той же транзакции, уникальность попытки дня на уровне БД). Redis — только rate-limit, кэш конфигов и поколение кэша. Уточняет карточку game в [services.md](../architecture/services.md) («горячие сессии в Redis») — нужен ADR | Redis + сброс в Postgres по завершении: при потере Redis теряются дейлики, нужен второй путь записи для outbox. Нагрузка этапа (один ход = одна строка, optimistic `version`) укладывается в p95 < 150 мс |
| D13 | **Сервисные токены (закрывает D8 этапа 1):** client credentials в identity — `POST /internal/v1/service-tokens {client_id, client_secret, audience}` → JWT EdDSA, `aud = <вызываемый сервис>`, `sub = svc:<client_id>`, `scope`, TTL 600 с, те же ключи и JWKS. Клиенты и scope — в env identity (`GV_SERVICE_CLIENTS`, секреты хранятся как sha256). Вызывающий кэширует токен и обновляет на 50 % TTL (single-flight), вызываемый проверяет его тем же `JwtVerifier` со своим `audience` (`require_service(scope)`). Применяется к media_pipeline `/internal/v1/*` и identity `/internal/v1/admin-sessions*` | mTLS — после переезда на mesh (Kubernetes): на VPS это CA и ротация сертификатов в compose. Общий HMAC — нет срока, `aud` и scope, по секрету на каждую пару сервисов. `private_key_jwt` (каждый сервис подписывает сам) — нет зависимости от identity, но публичные ключи вызывающих приходится раздавать всем. Пользовательский токен не проходит internal (`aud = guessverse`), сервисный — public |
| D14 | **Шэр:** текст-сетка (как Wordle) + ссылка на главную, через Web Share API или буфер обмена. Карточка-картинка и OG-превью — этап 7 (SEO) | Серверная генерация PNG — отдельная работа и нагрузка без влияния на игру |

## 2. Шаг 0 — оркестратор до и параллельно с потоками

Шаг разбит на две части. **0a** (ветка `stage2/contracts`) блокирует старт потоков. **0b** (ветка `stage2/platform`) оркестратор делает параллельно с потоками; она вливается до слияния B, потому что game вызывает media_pipeline с сервисным токеном.

### 2.1. Реестр, инфраструктура, CI (0a)
- `infra/services.toml`: состав не меняется (game: `database`, `redis`, `kafka`; bff_web: `redis`). Фронтенд в реестр не входит — у него свой compose-файл (поток D).
- `.importlinter.j2`: контракт `game_kernel-pure` (`gv_game_kernel` не импортирует `fastapi`, `sqlalchemy`, `redis`, `httpx`, `aiokafka`, `aio_pika`, `pydantic_settings`, `gv_db`, `gv_messaging`, `gv_service`), такой же `text-pure` для `gv_text`; затем `gv gen`.
- `gv contracts check` обходит `contracts/modes/**` (совместимость с `main`, как `contracts/clues`). Новый экспорт: снимок OpenAPI game и bff_web со схемами из §2.6.
- `tools/gv`: `gv test frontend` → `pnpm -C frontend test` (+ `--level e2e` → Playwright); в `gv test --changed` учитывается `frontend/**`.
- `.github/workflows/ci.yml`: job `frontend` (Node 24, pnpm cache, `lint`, `typecheck`, `test`, `build`, Playwright c `PLAYWRIGHT_BROWSERS_PATH`); job `e2e-smoke` (compose + `e2e/daily.spec.ts`) — на `main` и по метке PR. Первый живой прогон integration OpenSearch и `nginx -t` на nginx 1.28 — хвост этапа 1.

### 2.2. Зависимости (0a) — сразу во все `pyproject.toml`, один `uv lock`

| Пакет | Куда |
|---|---|
| `pydantic>=2.9` | `libs/game_kernel` (больше ничего: ядро без IO) |
| — (stdlib) | `libs/text` |
| `gv-game-kernel`, `gv-text`, `httpx>=0.27`, `pyyaml>=6` | game |
| `httpx>=0.27` | bff_web, `libs/auth` (клиент сервисных токенов) |
| `gv-text` | catalog |
| `gv-game-kernel` | testkit (DSL движка `gv_testkit/game.py`) |

В потоках `uv.lock` не меняется. `frontend/pnpm-lock.yaml` — собственность D.

### 2.3. Новые и общие библиотеки (0a)
- **`libs/text`** (`gv_text`): перенос `TitleNormalizer`, `NormalizerConfig`, `translit` из `catalog_service/domain/normalization.py` без изменения поведения. В catalog остаётся реэкспорт, golden-таблица переезжает в `libs/text/tests` (catalog оставляет свою копию как регресс). Владелец после 0a — поток A.
- **`libs/core`**: порт `gv_core.random.Random` (`randrange`, `sample`, `shuffle`, `random`) + `SystemRandom` + `SeededRandom(seed: bytes)`; в testkit — `FakeRandom`. [03-code-standards §3](../03-code-standards.md) требует порт, а его нет.
- **`libs/game_kernel`** (`gv_game_kernel`) — скелет. Реализации плагинов пишет поток A.
  - `spec.py`: Pydantic-модели `ModeConfig`, `SourceSpec`, `ClueSpec`, `AnswerSpec`, `ScoringSpec`, `SessionSpec` строго по `contracts/modes/mode_config.v1.json`; `params` — `dict`, их валидирует плагин.
  - `refs.py`: `MediaItemRef` (id, domain, kind, title, year, aliases: normalized[], persons[{id, name, role}], genres, popularity), `AssetRef` (asset_set_id, levels_count).
  - `ports.py`: `AssetPort.link(asset: AssetRef, level, subject) -> ClueAsset` (async), `CandidatePort.candidates(query: PoolQuery, limit) -> list[MediaItemRef]` (async), `CollectionPort` (async; адаптер — этап 3).
  - `plugins.py` (Protocol): `ClueType` (`key`, `domains`, `params_schema`, `supported_answer_formats`, `levels(params) -> list[LevelSpec]`, `reveal(...) -> ClueView`, `final_view(...)` для ревила), `Source` (`key`, `params_schema`, `async draw(ctx: DrawContext, params, n) -> list[MediaItemRef]`), `AnswerMatcher` (`match(answer: AnswerInput, item, ctx) -> MatchResult`), `PartialRule` (`kind`, `domains`, `check(...)`), `Scorer` (`score(outcome: RoundOutcome, params) -> int`, `max_score(params, levels) -> int`).
  - `round.py`: `RoundState` (неизменяемый: `level`, `attempts_on_level`, `guesses`, `outcome`, `partial_awarded_level`) и сигнатуры переходов `apply_guess`, `apply_skip`, `give_up`.
  - `registry.py`: `PluginRegistry` (`register_*`, дубликат ключа → ошибка), `EngineFactory.build(config) -> ModeEngine` и `validate(config) -> list[ConfigIssue(path, code)]` — сигнатуры и тесты на неизвестный ключ.
  - `params.py`: `canonical_json`, `params_hash` — повтор алгоритма `media_pipeline_service/domain/params.py`. Golden-векторы — `contracts/clues/params_hash.golden.json` (тест есть и в media_pipeline, и в ядре).
  - `errors.py`: `ConfigError`, `RuleViolation` (наследники `gv_core.errors`, коды `game.config_invalid`, `game.level_locked`, `game.round_finished`).
- **`libs/service`** (хвост этапа 1):
  - `Infrastructure.add_closer(fn)` — закрытие ресурсов процесса в обратном порядке в `close()` для **всех** команд (consumer и worker тоже). media_pipeline переводит на него `RabbitTaskQueue`.
  - `ServiceDefinition.build_periodic_jobs: Callable[[Infrastructure], list[PeriodicJob]]`, `PeriodicJob(name, interval_s, jitter_s, run)`. Задачи крутятся в процессе **relay** (он уже есть у каждого сервиса с БД и запускается в `all`) под `pg_try_advisory_xact_lock(crc32("<service>:<job>"))`: несколько реплик безопасны, compose и `gv gen` не меняются. Метрики `gv_periodic_job_runs_total{job,result}` и `..._duration_seconds`; исключение пишется в лог, цикл продолжается.
  - Встроенные задачи для всех сервисов с БД: `outbox.purge_published` и очистка inbox старше `GV_RETENTION__OUTBOX_DAYS = 7` ([data.md §4](../architecture/data.md)); `idempotency.purge` (`purge_older_than`, `GV_IDEMPOTENCY__RETENTION_H = 72`) — если в `tables` есть `idempotency_keys`. Так закрывается хвост «таблица растёт» в identity, catalog и media_pipeline без правок в сервисах.
- **`libs/messaging`** (хвост): протокол `EventProcessorPort` вместо `EventProcessor` в сигнатуре `KafkaEventConsumer` (admin перестаёт использовать `cast` — это делает поток E).
- **`libs/auth`** (0a — только интерфейсы и Principal, 0b — реализация):
  - `Principal.is_guest` (claim `gst`) и `Principal.subject_kind` (`user | guest`); `require_user` (гость → 403 `auth.guest_forbidden`).
  - `ServicePrincipal(client_id, scopes)`, `ServiceAuthenticator(verifier).require_service(*scopes)`, `ServiceTokenProvider(token_url, client_id, secret, audience)` (httpx, кэш, обновление на 50 % TTL, single-flight, при отказе identity — старый токен до `exp`).
- **testkit:** `TestTokenIssuer.issue_guest()`, `TestTokenIssuer.issue_service(client_id, audience, scopes)`, фикстура `service_token`; `FakeRandom`; `run_periodic_job(definition, name)` для тестов задач.

### 2.4. Схемы событий (0a)

Конверт и правила — как в этапе 1 (§2.4): `event_type = <producer>.<Event>`, `event_version = 1`, `version` в payload монотонна по агрегату. Время — RFC 3339 UTC, дата дня — `YYYY-MM-DD`.

`game`, топик `game.session.v1`, `aggregate_id = session_id`:
- `GameFinished`:
  - `session_id`, `version`, `user_id: uuid|null` (null у гостя), `is_guest: bool`;
  - `mode_key`, `mode_version`, `domain`, `source_type`, `clue_type`, `day: date|null`;
  - `score`, `max_score`, `started_at`, `finished_at`;
  - `rounds: [{index, media_item_id, outcome: "exact"|"partial"|"miss"|"gave_up"|"void", level: int|null, points, elapsed_ms: int|null}]`.
- `DailyCompleted` (только пользователи, не гости): `session_id`, `version`, `user_id`, `mode_key`, `mode_version`, `domain`, `day`, `score`, `max_score`, `exact_count`, `rounds_count`. Потребители (серии в rating, награды в progression) — этапы 4–5.

`game`, топик `game.mode_config.v1`, `aggregate_id = mode_key`:
- `ModeConfigPublished`: `mode_key`, `version` (= версия конфига), `domain`, `body_hash`, `actor_id`, `published_at`, `rolled_back_from: int|null`.
- `ModeConfigArchived`: `mode_key`, `version`, `actor_id`, `archived_at`.

Примеры — `contracts/events/game/examples/*.v1.json`. Потребляемые game события (примеры кладёт B в `tests/contract/consumed/`): `catalog.MediaItemCreated|Updated|Blocked|Unblocked`, `media_pipeline.AssetsReady|AssetsFailed`, `identity.UserDeleted`. Изменение `identity.UserRegistered` не требуется.

### 2.5. Контракт конфига режима (0a) — `contracts/modes/`
- `mode_config.v1.json`:
  - `key` (`^(music|film)\.[a-z0-9_]+\.[a-z0-9_]+$`), `title_i18n` (ключи), `domain`, `description_i18n?`;
  - `source {type, params}`, `clue {type, params}`, `answer {format: "classic", matcher {type, params}, partial: [kind]}`, `scoring {type, params}`;
  - `session {kind: "solo", rounds: 1..50, attempts_per_level: 1..5, skip_allowed, give_up_allowed, allow_guest, abandon_after_min}`;
  - `rewards` (проходит без проверки, используется с этапа 5), `features` (объект флагов).
  - Поля `version` и `status` задаёт сервис, а не автор.
- Параметры плагинов — `params/source.daily.v1.json` (`size`, `timezone`, `schedule_ahead_days`, `exclude_recent_days`, `pool`), `params/source.random.v1.json` (`pool`, `presets: [{id, title_i18n, filters}]`), `params/scorer.by_level.v1.json` (`points[]`, `partial_ratio`), `params/scorer.by_speed.v1.json` (`max_points`, `min_points`, `window_s`, `partial_ratio`), `params/matcher.title_alias.v1.json` (`typo: [{min_len, max_distance}]`, `accept_same_title`). Параметры загадок — уже в `contracts/clues/*`.
- `pool` — общий фрагмент `params/pool.v1.json`: `genres_any[]`, `years {from, to}`, `min_popularity`, `kinds[]`, `min_playable` (минимум для публикации).
- Сиды для валидации в CI — `seeds/game/modes/*.yaml` (4 режима из §0; их наполняет B, оркестратор кладёт черновики).

### 2.6. API (0a: схемы Pydantic и ручки-заглушки с 501 `common.not_implemented`, снимок OpenAPI экспортируется сразу; дальше файлы принадлежат потокам)

**game, public** (`/api/game/v1`; мутации — cookie + CSRF или Bearer, `Idempotency-Key`; ответы `Cache-Control: no-store`):
- `GET /v1/modes?domain=` → `[{key, version, domain, title_key, source_type, clue_type, rounds, levels: [{index, hint}], allow_guest, presets?: [{id, title_key}]}]`, публичный, `max-age=30`.
- `POST /v1/sessions {mode_key, preset_id?}`:
  - 201 `SessionSnapshot`; для дейлика при незавершённой сессии дня — 200 с ней же;
  - ошибки: 409 `game.daily_already_played` (`details.session_id`), 403 `game.guest_not_allowed`, 404 `game.mode_not_found`, 503 `game.daily_not_ready`.
- `GET /v1/sessions/{id}` → `SessionSnapshot`:
  - `id`, `version`, `status: active|finished|abandoned`, `mode {key, version, domain, clue_type}`, `day?`, `score`, `max_score`, `current_round`;
  - `rounds: [{index, status: pending|active|finished|void, level, levels_count, guesses: [{kind: item|text|skip, display, result}], points?, reveal?}]`. Поле `reveal` есть только у завершённого раунда.
- `POST /v1/sessions/{id}/rounds/{index}/clue {level}` → `{level, kind: audio|image, url, expires_at, mime, meta: {seconds?|blur?}}`. Ошибки: 403 `game.level_locked`, 410 `game.asset_unavailable` (раунд становится `void`), 429, 503 `game.assets_unavailable`. POST, потому что каждая выдача пишется в журнал media-pipeline и ограничена лимитом.
- `POST /v1/sessions/{id}/guesses {round_index, expected_version, answer: {media_item_id} | {text: str(1..120)}}` → `{result: exact|partial|miss, round, session: SessionSnapshot}`. Ошибки: 409 `game.desync` (в `details` — актуальный снимок), 409 `game.round_finished`, 429.
- `POST /v1/sessions/{id}/skips {round_index, expected_version}` и `POST /v1/sessions/{id}/rounds/{index}/give-up {expected_version}` → то же.
- `GET /v1/sessions/{id}/result` (только `finished`) → `{score, max_score, rounds: [{index, outcome, level, points, reveal}], share: {text}, daily_stats?: [{index, exact_pct}]}`.
- `GET /v1/daily?domain=` → `[{mode_key, day, status: not_started|in_progress|finished, session_id?, score?, max_score, next_reset_at}]` для текущего субъекта (гость или пользователь).
- `reveal` = `{media_item_id, title, year, persons: [{name, role}], final_clue: {kind, url, expires_at, mime}}`.

**game, admin** (`/admin/v1`, `admin.access` + право, `Idempotency-Key` и `reason` на мутациях — [ADR 0015](../adr/0015-admin-idempotency-and-asset-event-order.md)):
- конфиги:
  - `GET /admin/v1/mode-configs?domain=&status=` и `GET /admin/v1/mode-configs/{key}` — версии и черновик (`config.modes.view`);
  - `GET /admin/v1/mode-configs/{key}/versions/{version}`;
  - `GET /admin/v1/mode-configs/schema` — сводная JSON Schema для подсказок редактора;
  - `POST /admin/v1/mode-configs/validate {body}` → `{ok, errors: [{path, code, message_key}], warnings: [{code}], pool: {playable_count}}`;
  - `PUT /admin/v1/mode-configs/{key}/draft {body, base_revision, reason}` — сохраняет и невалидный черновик, ответ несёт итог валидации (`config.modes.edit`);
  - `POST .../{key}/publish {draft_revision, reason}`, `POST .../{key}/rollback {to_version, reason}`, `POST .../{key}/archive {reason}` — `config.modes.publish`.
- наборы дня:
  - `GET /admin/v1/daily-sets?mode_key=&from=&to=` (`game.daily.view`) → `[{mode_key, day, mode_version, status: scheduled|locked|failed, items: [{index, media_item_id, title, year, playable}]}]`;
  - `PUT /admin/v1/daily-sets/{mode_key}/{day}/items/{index} {media_item_id, reason}` — 409 `game.daily_locked`, `game.item_not_playable`, `game.item_duplicate` (`game.daily.curate`);
  - `POST /admin/v1/daily-sets/{mode_key}/{day}/regenerate {reason}` (`game.daily.curate`);
  - `GET /admin/v1/projection/items?q=&domain=&playable_for_mode=` — поиск кандидатов на замену в проекции game с признаком играбельности (`game.daily.view`).

**bff_web, public** (`/api/web/v1`, только GET; токен берётся из cookie `gv_access` или Bearer и передаётся дальше как Bearer):
- `GET /v1/session` → `{subject: {kind: user|guest, display_name?}|null}`;
- `GET /v1/pages/home` → `{blocks: {dailies: Block<[DailyCard]>, modes: Block<[ModeCard]>}}`, где `Block<T> = {status: ok|unavailable, data?: T}`;
- `GET /v1/pages/solo?domain=` → `{blocks: {daily, random}}`;
- `GET /v1/pages/results/{session_id}` → итог из game + карточки catalog `/v1/items/{id}` (жанры, персоны, внешние ссылки).

**identity** (0a: заглушки и схемы; реализация — 0b и C):
- `POST /internal/v1/service-tokens {client_id, client_secret, audience}` → `{access_token, token_type: "Bearer", expires_in}`; ошибки 401 `auth.client_invalid`, 403 `auth.audience_forbidden`; лимит по `client_id`.
- `POST /v1/auth/guest` → 204, cookie `gv_access`, `gv_refresh`, `gv_csrf` (как при входе); `require_same_origin`, лимит по IP. При уже действующей сессии — 409 `auth.already_authenticated`.

### 2.7. Права (0a, миграция identity `0005_seed_game_perms.py`)
`config.modes.view`, `config.modes.edit`, `config.modes.publish`, `game.daily.view`, `game.daily.curate`. admin — все права; moderator — `config.modes.view`, `game.daily.view`, `game.daily.curate`.

### 2.8. Шаг 0b — сервисные токены и хвосты (оркестратор, ветка `stage2/platform`)
1. identity: `IssueServiceToken` (сравнение sha256 секрета через `hmac.compare_digest`, разрешённые `audience` и `scope` у клиента, лимит `GV_SERVICE_TOKEN_LIMIT__PER_CLIENT_PER_MIN = 60`), конфиг `GV_SERVICE_CLIENTS='[{"client_id":"game","secret_sha256":"…","audiences":{"media_pipeline":["assets.link"]}}, {"client_id":"admin","secret_sha256":"…","audiences":{"identity":["admin_sessions"]}}]'`; claims `iss`, `aud`, `sub = svc:<id>`, `scope`, `iat`, `exp`, `jti`, без `sid`, `roles` и `perms`.
2. `libs/auth`: реализация `ServiceTokenProvider` и `require_service` из §2.3.
3. media_pipeline: `/internal/v1/*` → `require_service("assets.link")`. В журнал `link_issuance` пишется ещё и `client_id`. `GV_INTERNAL_AUTH__AUDIENCE` по умолчанию равен `service_name`.
4. identity `/internal/v1/admin-sessions*` → `require_service("admin_sessions")`; admin получает `ServiceTokenProvider` (вместе с потоком E: E меняет только `services/admin`, после слияния 0b).
5. `env.example.j2` и `gv dev secrets`: случайные `GV_SERVICE_AUTH__CLIENT_SECRET` для game и admin, их sha256 — в `GV_SERVICE_CLIENTS` identity.
6. Подключение `add_closer` в media_pipeline; проверка встроенных `PeriodicJob` (purge) на identity, catalog, media_pipeline.
7. Тесты: component в identity (выдача, неверный секрет, чужая `audience`, лимит), в media_pipeline (без токена → 401, пользовательский JWT → 401, токен без scope → 403, токен game → 200), unit `ServiceTokenProvider` (кэш, single-flight, обновление, отказ identity), integration `PeriodicJob` (две реплики — один запуск).
8. Ревью: `security-discipline`, `auth-and-authorization`, агент `security-reviewer`, `finecomb` по diff identity и `libs/auth`.

### 2.9. Документация и ADR (оркестратор, по ходу)
- ADR 0016 «Сервисные токены через client credentials» (D13); ADR 0017 «Фронтенд: Next.js в `frontend/`, bff только чтение» (D1, D2, D4); ADR 0018 «Гостевые сессии в identity» (D5); ADR 0019 «Соло-сессии в Postgres» (D12, уточняет services.md); ADR 0020 «ModeConfig ↔ ассеты по `params_hash`, очки в Scorer» (D7, уточняет 02-game-engine §4).
- Обновить `02-game-engine.md` (§4–5: пример конфига с `scoring.params.points`; периодические задачи), `architecture/security.md` (§3, §7), `architecture/services.md` (game, bff-web, матрица вызовов: bff → game, identity, catalog).

## 3. Потоки и владение файлами

| Поток | Ветка | Может менять |
|---|---|---|
| **A** game_kernel | `stage2/kernel` | `libs/game_kernel/**` (кроме `spec.py` — его меняет только шаг 0), `libs/text/**`, `libs/testkit/src/gv_testkit/game.py` |
| **B** game | `stage2/game` | `services/game/**`, `contracts/openapi/game.json`, `seeds/game/**`, `seeds/media/**` |
| **C** bff + гости | `stage2/bff-guest` | `services/bff_web/**`, `contracts/openapi/bff_web.json`; в identity — только гостевые сессии: `api/public/guest.py`, `application/commands/guest*.py`, `domain/guest*.py`, миграция `0006_guest_sessions.py`, её тесты; `contracts/openapi/identity.json` |
| **D** фронтенд | `stage2/web` | `frontend/**`, `e2e/**`, `infra/compose/compose.web.yml`, `infra/docker/web.Dockerfile`, `infra/nginx/nginx.conf` (только `location /` и upstream web), `infra/nginx/snippets/web-*.conf` |
| **E** admin | `stage2/admin` | `services/admin/**` |
| Оркестратор | `stage2/contracts`, `stage2/platform`, слияния | `infra/services.toml`, корневой `pyproject.toml`, `uv.lock`, `contracts/events/**`, `contracts/modes/**`, `contracts/clues/**`, `libs/{core,db,messaging,service,auth}/**`, `libs/testkit/**` (кроме `game.py`), `libs/game_kernel/src/gv_game_kernel/spec.py`, `tools/gv/**`, `.github/**`, `docs/**`, ADR; в 0b — `services/identity/**` (кроме файлов C), `services/media_pipeline/**`, `services/catalog/**` (реэкспорт нормализатора) |

Правила — как в этапе 1: чужое и общее меняет только оркестратор по запросу потока. Каждый поток правит только `CLAUDE.md` своего сервиса. Изменение схемы события или `contracts/modes` — это новый шаг 0. C начинает правки identity после слияния 0b (до этого — домен и тесты гостевой сессии на фейках). Новые права и настройки окружения потоки запрашивают у оркестратора.

## 4. Поток A — `libs/game_kernel` и `libs/text`

**Цель:** правила игры описаны один раз и без IO; game (и match в этапе 4) только оркестрирует.

**`libs/text`:** `damerau_levenshtein(a, b, max_distance)` с ранним выходом; `NormalizerConfig.replacements` (`&` / «и» / and → общий токен, [02-game-engine §7](../02-game-engine.md)) — новые правила только через golden-таблицу. Unit: идемпотентность (Hypothesis), golden из catalog без изменений, метрика расстояния (симметрия, неравенство треугольника на выборке).

**Плагины (регистрация — `register_builtin(registry)`, без `if type ==`):**
- ClueType: `audio_intro`, `frame_progressive`, `poster_masked`. Модели параметров повторяют `contracts/clues/*` (contract-тест). `levels()` отдаёт `LevelSpec(index, hint)`, где `hint` — `{seconds}` или `{blur_px, crop}` для UI. `reveal()` вызывает `AssetPort.link` для уровня, `final_view()` — для последнего уровня (ревил). `supported_answer_formats = {"classic"}`.
- Source:
  - `daily`: `PoolQuery` из `pool` + `playable_for = (clue_type, params_hash)` + исключение единиц из наборов последних `exclude_recent_days`. Кандидаты сортируются по id, затем `SeededRandom(hmac(secret, mode_key|day)).sample(n)` — детерминированно и непредсказуемо без секрета;
  - `random`: `pool` ∧ фильтры выбранного `preset`, seed сессии;
  - `collection`: реализация на `CollectionPort` и тесты. В game на этапе 2 не регистрируется (проекции сборников — этап 3), поэтому конфиг с ним не проходит валидацию game.
- AnswerMatcher `title_alias`:
  - `{media_item_id}` → `exact`, если id совпал, или (`accept_same_title`) нормализованное основное название выбранной единицы совпадает с алиасом вида `title|alt` загаданной;
  - `{text}` → нормализация `gv_text`, сравнение с `aliases.normalized` с допуском по таблице `typo`;
  - иначе — проверка `PartialRule` из `answer.partial`.
- PartialRule `artist` (только `music`): у выбранной единицы есть общий `person` с ролью `artist`, или текст совпал с нормализованным именем исполнителя.
- Scorer:
  - `by_level`: `points[level-1]`; частичный ответ — `floor(points[level_of_partial-1] × partial_ratio)`, начисляется один раз, раунд продолжается. Итог раунда — `max(exact, partial)`, `max_score = points[0] × rounds`;
  - `by_speed`: `max(min_points, round(max_points × clamp(1 − t/window_s)))`, × `partial_ratio` для частичного; `t` из `RoundOutcome.elapsed_ms`.
- `round.py`: переходы `RoundState` по `SessionSpec`: верный ответ завершает раунд, промах или пропуск — следующий уровень после `attempts_per_level` ответов, после последнего уровня — `miss`. `give_up` → `gave_up`, `void` — когда ассет недоступен.
- `EngineFactory.validate`:
  - каждый ключ есть в реестре;
  - `params` проходят `params_schema` плагина;
  - `domain ∈ clue.domains`; `answer.format` поддерживается ClueType; `session.kind ∈ supported_sessions`;
  - `len(scoring.points) == len(clue.levels)`; `partial`-правила применимы к домену;
  - все ошибки собираются, у каждой есть JSON-путь.
- `ModeEngine`: `levels`, `draw`, `check(answer)`, `advance`, `score`, `max_score` — фасад для game.

**`gv_testkit/game.py`:** `FakeAssetPort` (журнал выдач, сбои 410/503), `FakeCandidatePort`, фабрика `media_item(...)`, DSL `given_mode(cfg).with_items(...).when_guess(...).when_skip().then_round(...).then_score(...)`.

**Тесты (unit, ≥ 90 % покрытия):**
- property-based: очки не растут с уровнем; `by_speed ≥ min_points`; раунд завершается не более чем за `levels × attempts_per_level` шагов; детерминизм `draw` при одном seed;
- golden matcher (`tests/golden/matcher.yaml`): опечатки, ё/е, транслит, дубли названий, частичный ответ по исполнителю;
- валидатор на сидах `seeds/game/modes` и на сломанных конфигах (по коду ошибки на каждое правило);
- contract: модели ↔ `contracts/modes/**`, `contracts/clues/**`, golden `params_hash`;
- import-linter `game_kernel-pure`.

**Готово, когда** `gv test libs/game_kernel`, `gv test libs/text`, `gv test catalog` (регресс нормализации) и `gv contracts check` зелёные. **Риски:** семантику частичного ответа нужно согласовать до B (фиксируется golden-тестом в первый день); производительность допуска опечаток — ограничение длины ввода и ранний выход.

## 5. Поток B — сервис game

**Домен** (`game_service/domain/`):
- `ModeConfigRecord`: опубликованные версии неизменяемы; черновик с `revision` (оптимистичная блокировка); `publish` (только валидный черновик) → новая версия; `rollback(to)` = копия старой как новая версия; `archive` убирает режим из `GET /v1/modes`, сессии доигрываются.
- `DailySet`:
  - `(mode_key, day)`, `mode_version`, `items[size]`, статусы `scheduled → locked`, `failed`;
  - замена единицы — только в `scheduled`, без дублей, единица играбельна;
  - `lock` в начале дня; если единица заблокирована до `lock`, задача планирования заменяет её автоматически.
- `GameSession`:
  - `PlayerRef(kind: user|guest, id)`, `mode_key@version`, `source_ctx` (`day` | `preset_id`), `seed`, `status`, `version`, `rounds[]`;
  - раунд = `media_item_id`, `asset_ref`, `RoundState` ядра, `started_at` (первая выдача ассета);
  - ход меняет `version`; последний раунд → `finished` + доменное событие;
  - `abandon` по таймауту; раунд `void` не входит в `max_score`.
- Проекции: `CatalogItem` (из `MediaItem*`, по `version`), `ClueAssets` (из `Assets*`, по `version` и `media_item_id`, [ADR 0015](../adr/0015-admin-idempotency-and-asset-event-order.md)). Unit: инварианты агрегатов, переходы, устаревшие события.

**Application:**
- команды конфигов: `SaveDraft`, `ValidateModeConfig` (ядро + размер пула по проекции), `PublishModeConfig`, `RollbackModeConfig`, `ArchiveModeConfig`, `BootstrapModes`;
- команды наборов дня: `ScheduleDailySets` (периодическая), `LockDueDailySets`, `ReplaceDailyItem`, `RegenerateDailySet`;
- команды игры: `StartSession`, `IssueClue`, `SubmitGuess`, `SkipLevel`, `GiveUpRound`, `AbandonStaleSessions` (периодическая);
- обработчики событий: `ApplyCatalogEvent`, `ApplyAssetsEvent`, `ForgetUser` (`UserDeleted` → `player_id` обнуляется, результаты остаются для статистики);
- запросы: `ListModes`, `GetSession`, `GetResult`, `GetDailyStatus`, админские списки и поиск по проекции;
- порты: `ModeConfigRepository`, `DailySetRepository`, `SessionRepository`, `ProjectionRepository` (+ реализация `CandidatePort`), `AssetLinkClient` (реализация `AssetPort`), `RateLimiter`, `ModeConfigCache`, `IdempotencyStore`, `Clock`, `Random`.

**Инфраструктура:**
- SQL-репозитории. `SessionRepository.get_for_update` + проверка `expected_version` → `game.desync`.
- `MediaPipelineAssetClient`: httpx, таймаут 2 с, 3 ретрая только на 503 и сеть, circuit breaker, токен из `ServiceTokenProvider` (aud `media_pipeline`). Маппинг: 410 → раунд `void`, 409 и 503 → 503 `game.assets_unavailable`, 429 → 429.
- `RedisRateLimiter` (fail-open с метрикой: попытки и так ограничены правилами раунда; ассеты защищает fail-closed лимитер media).
- `ModeConfigCache`: в процессе, TTL 60 с + поколение в Redis (`INCR game:modes:gen` после публикации — как кэш автокомплита catalog). Свой топик game не читает.
- `CandidatePort` на SQL: фильтры по проекции + `EXISTS clue_assets(status=ready, params_hash)`; на этапе 2 выборка целиком из пула (сотни единиц), сэмплирование в ядре.

**Таблицы** (`0002_game_core.py`, expand-only):
- `mode_configs` (PK `(key, version)`, `domain`, `status`, `body jsonb`, `body_hash`, `actor_id`, `reason`, `created_at`); `mode_config_drafts` (`key` PK, `body`, `revision`, `validation jsonb`, `updated_by`, `updated_at`);
- `catalog_items` (`media_item_id` PK, `domain`, `kind`, `status`, `version`, `title`, `year`, `aliases jsonb`, `persons jsonb`, `genres text[]` GIN, `popularity`); `clue_assets` (PK `(media_item_id, clue_type, params_hash)`, `asset_set_id`, `levels_count`, `status`, `version`);
- `daily_sets` (PK `(mode_key, day)`, `mode_version`, `status`, `locked_at`, `generated_by`); `daily_set_items` (PK `(mode_key, day, idx)`, `media_item_id`, индекс `(mode_key, media_item_id, day)` для `exclude_recent_days`);
- `game_sessions` (`id` uuid7, `player_kind`, `player_id`, `mode_key`, `mode_version`, `source_type`, `day`, `preset_id`, `seed`, `status`, `score`, `max_score`, `version`, `state jsonb`, `started_at`, `finished_at`, `last_move_at`). Частичный unique `(mode_key, day, player_kind, player_id) WHERE day IS NOT NULL` — одна попытка дня. Индекс `(player_kind, player_id, started_at desc)`. Партиционирование по месяцу — когда понадобится ([data.md §4](../architecture/data.md)).
- `daily_round_stats` (PK `(mode_key, day, idx)`, `played`, `exact`, `partial`) — upsert в транзакции завершения, только пользователи;
- `idempotency_keys` (`gv_db.idempotency`; `actor_id` = id субъекта, у гостя тоже).

**События:** публикует `GameFinished` (все), `DailyCompleted` (пользователь + дейлик), `ModeConfigPublished`, `ModeConfigArchived` — outbox в транзакции. Слушает `catalog.media_item.v1`, `media_pipeline.asset_set.v1`, `identity.user.v1`.

**API:** §2.6. Роутер ≤ 15 строк, owner-check в application: чужая или несуществующая сессия → 404 `game.session_not_found` (не 403). Мутации public — `require_csrf` при cookie-токене, `Idempotency-Key` обязателен (повтор хода возвращает сохранённый ответ). Гость — `PlayerRef(guest)`; `allow_guest = false` → 403.

**Сиды:**
- `seeds/game/modes/*.yaml` — 4 режима из §0. Дейлики: `size 5`, `exclude_recent_days 7`, `schedule_ahead_days 2`; random: пресеты по десятилетиям и жанрам.
- `seeds/media/` — синтетические тоны и абстрактные кадры для e2e (без прав третьих лиц) + `README` с порядком загрузки через admin. Пул каталога к концу этапа — 200 + 200 (план этапа 1, §5).

**Настройки:**
- media и сервисная аутентификация: `GV_GAME__MEDIA_URL = http://media_pipeline:8000`, `GV_SERVICE_AUTH__TOKEN_URL|CLIENT_ID=game|CLIENT_SECRET`;
- `GV_GAME__DAILY_SEED_SECRET` (secret), `GV_GAME__BOOTSTRAP_MODES_DIR` (только local/ci), `GV_GAME__CONFIG_CACHE_TTL_S = 60`;
- лимиты: `GV_GAME_LIMITS__GUESS_PER_MIN = 60`, `__CLUE_PER_MIN = 120`, `__SESSIONS_PER_HOUR = 60`, `__GUEST_SESSIONS_PER_IP_PER_HOUR = 30`;
- периодические задачи: `GV_GAME__SCHEDULE_INTERVAL_S = 900`, `GV_GAME__ABANDON_INTERVAL_S = 600`; `GV_PUBLIC_ORIGINS`.

**Тесты:**
- integration: репозитории (Postgres), идемпотентность и порядок проекций (повтор, старая `version`), уникальность попытки дня при гонке двух `StartSession`, `CandidatePort`, лимитер на Redis, `MediaPipelineAssetClient` на `stub_service("media_pipeline")` (контракт, токен в заголовке);
- component:
  - полный дейлик: примеры событий → проекция → `ScheduleDailySets` → старт → ассет → ходы → итог; `GameFinished` и `DailyCompleted` валидны по схеме;
  - повторный старт → 409 или возобновление;
  - IDOR → 404;
  - гость: нет `DailyCompleted`, нет статистики;
  - `level > current` → 403;
  - снимок без `media_item_id` до ревила (проверка по ключам JSON);
  - desync → 409 со снимком; повтор хода с тем же ключом → тот же ответ;
  - ассет 410 → раунд `void`, `max_score` уменьшен;
  - админ: публикация и откат с правами и без, невалидный конфиг → ошибки с путями, замена в `locked` → 409;
  - новая сессия случайного режима на v2, дейлик — на версии набора;
- contract: consumed-примеры, снимок OpenAPI, продюсерские события.

**Готово, когда** `gv test game` зелёный, покрытие domain ≥ 90 %, application ≥ 85 %, и проверки §9 закрыты. **Риски:** критический путь этапа — B стартует на скелете ядра и фейках `gv_testkit/game.py`, после слияния A делает rebase и прогоняет component-тесты на реальных плагинах. Объём B самый большой — конфиги и наборы дня делаются первыми (они нужны E), игровой цикл вторым.

## 6. Поток C — bff-web и гостевые сессии

**bff_web** (без БД; Redis — кэш):
- application: запросы `GetSession`, `GetHomePage`, `GetSoloPage`, `GetResultsPage`; порты `IdentityClient` (`/v1/me`), `GameClient` (`/v1/modes`, `/v1/daily`, `/v1/sessions/{id}/result`), `CatalogClient` (`/v1/items/{id}`), `PageCache`.
- infrastructure: httpx-клиенты по контрактам. Таймаут 1,5 с на вызов, `asyncio.gather` с ограничением 10, circuit breaker на сервис. Блок упавшего сервиса → `status: unavailable` (деградация из [services.md](../architecture/services.md)). Пользовательский токен передаётся как `Authorization: Bearer`; `X-Request-Id` и `traceparent` пробрасываются. Кэш: публичные режимы — 30 с; карточки catalog — 5 мин (Redis, ключ без пользователя). Личные блоки не кэшируются.
- API: §2.6. Публичные ответы — `Cache-Control: public, max-age=30, stale-while-revalidate=60`, личные — `private, no-store`. Мутаций нет, поэтому CSRF и идемпотентность не нужны (D4).
- Тесты: unit (сборка блоков, деградация); component (`stub_service` для identity, game и catalog по контрактам: всё ok; game недоступен → блок `unavailable`, ответ 200; гость; истёкший токен → `subject: null`, без 500; кэш карточки).

**identity — гостевая сессия** (зона повышенного контроля):
- домен: `Session.kind = user|guest`. У гостя `user_id = NULL` и `guest_id` uuid7 (CHECK на согласованность); ротация refresh — общая.
- `StartGuestSession`: `require_same_origin`, лимит `GV_GUEST__PER_IP_PER_HOUR = 20` (fail-open, как у входа), access TTL как у пользователя, refresh `GV_GUEST__REFRESH_TTL_S = 2592000`. JWT: `sub = guest_id`, `roles = ["guest"]`, `perms = []`, `gst = true`, `sid`.
- Вход или регистрация из гостевой сессии отзывают её (cookie перевыдаются). `GET /v1/me` для гостя → 403 `auth.guest_forbidden` (bff показывает гостя по claim).
- Периодическая задача: удаление истёкших гостевых сессий.
- Миграция `0006_guest_sessions.py` (expand: nullable `user_id`, `kind` с default `user`, `guest_id`).
- Тесты: component (выдача, refresh, повторное использование refresh → отзыв, лимит по IP, чужой Origin → 403, гостевой токен не проходит `require_user` и admin-API); метрика `identity_guest_sessions_started_total`.
- Ревью: `security-discipline`, `auth-and-authorization`, `security-reviewer`, `finecomb` по diff identity. **Требуется ручная проверка владельцем.**

**Готово, когда** `gv test bff_web`, `gv test identity`, `gv contracts check` зелёные и пройдено ревью.

## 7. Поток D — фронтенд

**Каркас:**
- `frontend/` — Next.js, TS strict, ESLint + Prettier, Tailwind v4, shadcn/ui, next-intl (`messages/ru.json`), TanStack Query.
- `openapi-typescript` + `openapi-fetch`: типы из `contracts/openapi/{game,bff_web,catalog,identity}.json` → `src/shared/api/schema/` (`pnpm gen:api`; CI проверяет, что сгенерированное совпадает с контрактами).
- Структура: `src/app/` (маршруты), `src/features/{auth,daily,solo,game,results}/`, `src/shared/{api,ui,i18n,lib}/`.

**Маршруты:**
- `/` — главная. SSR: каркас и режимы из bff. CSR: карточки дейликов со статусом и обратным отсчётом до `next_reset_at`.
- `/solo` — хаб: дейлики и случайный режим с пресетами по доменам.
- `/play/[modeKey]` — старт или возобновление сессии, затем `replace` на `/play/s/[sessionId]` (CSR, `dynamic = "force-dynamic"`).
- `/results/[sessionId]` — итог (через bff), строки по раундам, доля угадавших, «Поделиться» (текст-сетка, Web Share API или буфер обмена), «Случайный режим», у гостя — «Войти, чтобы сохранить прогресс».
- `/login`, `/register`, `/verify-email` — минимальные формы к API identity (вход по e-mail или нику; согласия из `GV_CONSENTS__REQUIRED`).

**Игровой экран:**
- `GameScreen`: индикатор раундов, история попыток (✗, ≈ частичный, ✓), поле ответа с автокомплитом (`/api/catalog/v1/autocomplete?domain=`, debounce 200 мс, ≤ 64 символов, клавиатурная навигация, ARIA combobox), «Пропустить (+N с)», «Сдаться».
- Подача загадки — по `clue.kind` из реестра компонентов (`audio` → `AudioClue`, `image` → `ImageClue`), без ветвлений по `clue_type`:
  - `AudioClue`: ассет уровня скачивается сразу после выдачи (TTL 60 с) в `Blob` → `<audio>`. Сегментированная шкала по `levels[].hint.seconds`, повтор без новой ссылки; при ошибке загрузки — повторная выдача (с лимитом);
  - `ImageClue`: прогрессивный кадр, плавная смена уровня, `prefers-reduced-motion`.
- Ревил-карточка после раунда (`final_clue`, название, год, персоны), кнопка «Далее».

**Сессия и ошибки:**
- Гость: если нет `gv_access`, перед стартом игры вызывается `POST /api/identity/v1/auth/guest`.
- Refresh — single-flight между вкладками (Web Locks API) на 401 `auth.token_expired`: повторное использование refresh отзывает сессию.
- `X-CSRF-Token` из cookie `gv_csrf`; `Idempotency-Key` (uuid) на каждый ход, повтор при сетевой ошибке — с тем же ключом.
- Ошибки: 409 `game.desync` → применить снимок из `details`; 409 `game.daily_already_played` → на результаты; 410 → «Недоступно — раунд не засчитан»; 429 → тост; сеть → ресинк `GET /sessions/{id}`.

**Инфраструктура:**
- `web.Dockerfile` (standalone, non-root, `HEALTHCHECK /healthz`); `compose.web.yml` (сервис `web`, `GV_BFF_INTERNAL_URL=http://bff_web:8000`).
- `nginx.conf`: `location /` → `gv_web` со своим сниппетом заголовков без CSP (CSP с nonce ставит middleware Next: `default-src 'self'; script-src 'self' 'nonce-…'; media-src 'self' blob:; img-src 'self' blob: data:; connect-src 'self'; frame-ancestors 'none'`). `/api/` и `/media/` не меняются.

**Тесты:**
- vitest + Testing Library + MSW: редьюсер игрового экрана, автокомплит (debounce, отмена устаревших запросов), single-flight refresh, повтор с тем же `Idempotency-Key`, шэр-текст, i18n-ключи без пропусков;
- Playwright (Chromium из `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers`) против `next build && next start` с моками API по контрактам (`frontend/e2e/mocks/`, MSW node). Сценарии: дейлик музыки (гость), «Кадр дня», desync-ресинк, повторный вход в пройденный дейлик, случайный с пресетом, вход и регистрация; `@axe-core/playwright` — без нарушений уровня serious;
- `e2e/daily.spec.ts` — полный стек на compose, только в CI (локально нет Docker).

**Готово, когда** `pnpm lint typecheck test build` и Playwright на моках зелёные, Lighthouse главной (mobile) ≥ 90 по performance и accessibility, `nginx -t` проходит. **Риски:** автовоспроизведение аудио блокируется браузером (воспроизведение только по жесту, это учитывает UX); короткий TTL ссылок (скачивание сразу после выдачи).

## 8. Поток E — admin: конфиги режимов и наборы дня

Отдельный поток оправдан: Django-код и свои тесты не пересекаются с B, контракт admin-API зафиксирован в 0a.

- `apps/game_config/`:
  - список режимов (ключ, домен, опубликованная версия, есть ли черновик);
  - карточка: версии, просмотр тела, diff черновика с опубликованной (`difflib` по каноничному YAML);
  - редактор черновика (YAML/JSON в `textarea` с моноширинным шрифтом, кнопка «Проверить» → `validate`, ошибки с путями рядом с полем, предупреждение о размере пула);
  - сохранение, публикация, откат к версии, архив — с `reason` и скрытым `idempotency_key`, `AuditEntry` при успехе и при отказе.
- `apps/game_daily/`: календарь наборов на `from..to` (по режиму), состав с признаком `playable`, замена единицы (поиск через `GET /admin/v1/projection/items`), перегенерация. `locked` — только чтение.
- Права Django — из perms §2.7. Пункты меню скрыты без права, ручки проверяют право сами.
- Хвосты: `mypy_django_plugin`; consumer без `cast` (протокол из 0a); `ServiceTokenProvider` для `/internal/v1/admin-sessions` (после 0b).
- Тесты (pytest-django, `stub_service("game")` по контракту): публикация отправляет `Idempotency-Key` и `reason`; ошибки валидации отображаются; нет права → 403 и пункт скрыт; замена в `locked` → сообщение об ошибке; аудит пишется при отказе сервиса; вход в админку с сервисным токеном (заглушка identity проверяет заголовок).

**Готово, когда** `gv test admin` и `mypy` admin с плагином зелёные.

## 9. Обязательные проверки безопасности

1. **Античит (D11):**
   - в ответах public game до ревила нет `media_item_id`, `title` и `asset_set_id` — component-тест обходит JSON рекурсивно;
   - выдача ассета: `level ≤ current`, раунд активен, сессия своя;
   - `reveal` и `final_clue` — только у завершённого раунда;
   - ключи объектов без id (этап 1);
   - `Cache-Control: no-store` на ответах game.
2. **IDOR:** сессия, ассет и результат доступны только своему субъекту (`player_kind` + `player_id` из `Principal`). Ответ — 404 для чужой и несуществующей сессии одинаково. Перебор `session_id` бесполезен (uuid7 + проверка владельца).
3. **Целостность хода:** `expected_version` → 409; `Idempotency-Key` в транзакции хода (`gv_db.idempotency`); строка сессии под `FOR UPDATE`; число попыток ограничено правилами раунда.
4. **Rate limit:** ходы, выдача ассетов, старт сессий — по субъекту; гостевые сессии — по IP (game и identity). Лимит ссылок media — fail-closed. Метрика «точный ответ быстрее X мс на уровне 1» — флаг в лог (детектор аномалий — этап 5).
5. **Гости:** гостевой токен без прав не проходит admin-API, `require_user` и internal; гость не попадает в статистику и `DailyCompleted`; лимит выдачи гостевых сессий по IP; истёкшие гостевые сессии чистятся.
6. **Сервисные токены (D13):** `aud` = вызываемый сервис, scope на ручку; секреты только из env (sha256 у identity); токены не логируются (маскирование `gv_observability`, тест); пользовательский JWT на internal → 401, сервисный на public → 401.
7. **CSRF и cookie:** все мутации game с cookie — `require_csrf`; `POST /v1/auth/guest` — `require_same_origin`. bff без мутаций. CSP фронта с nonce, `frame-ancestors 'none'`.
8. **Admin-API game:** `admin.access` + конкретное право, `reason`, `Idempotency-Key`, аудит в game (`mode_configs.actor_id/reason`, журнал замен набора дня) и в admin.
9. **Валидация конфига:** публикуется только валидный конфиг; тела `jsonb` ограничены по размеру (`GV_GAME__MODE_CONFIG_MAX_BYTES = 65536`); неизвестные поля запрещены схемой.
10. **ПДн:** в событиях game нет e-mail и ника; `UserDeleted` обнуляет `player_id`.
11. **Процедура** (`docs/10-ai-assistant.md` §3):
    - 0b и C (identity, `libs/auth`) — `security-discipline`, `auth-and-authorization`, `security-reviewer`, `finecomb`;
    - B, D, E — `security-discipline`;
    - новые OpenAPI (game, bff_web, identity) — `42crunch-audit`;
    - D — ревью CSP и обработки токенов.

## 10. Что НЕ входит (этап 3 и дальше)

- Лента дейлика, прогнозы и победитель прогноза, серии, архив дней, предложения треков — этапы 3–5 (данные дня хранятся по дате, архив добавляется без миграций).
- Перенос гостевых результатов в аккаунт, опыт и валюта за игру (`rewards`) — этап 5.
- Формат `lite` и `pick`, режимы «сборник», марафон, «больше/меньше», топ-32; остальные ClueType — этап 3.
- `ClueAssetsRequested` и генерация ассетов под новый конфиг — этап 3 (на этапе 2 конфиг использует существующие профили).
- «Смотреть позже» и сохранение из ревила (library) — этап 3.
- Форма редактора конфигов по JSON Schema, TOTP в админке, реплики в админке — этап 5.
- Centrifugo и realtime на фронте, лидерборды, онлайн — этап 4.
- Карточка-картинка шэра, OG, PWA, SEO, нагрузочные тесты k6 — этап 7.
- mTLS и проверка JWT на gateway — после переезда в Kubernetes/Envoy.

## 11. Риски

| Риск | Мера |
|---|---|
| B — критический путь и самый большой поток | Скелет ядра и фейки в 0a; конфиги и наборы дня в B первыми; A сливается первым; component-тесты B на реальных плагинах сразу после слияния A |
| Расхождение `params_hash` в game и media-pipeline | Golden-векторы в `contracts/clues`, тест с обеих сторон; при публикации проверяется пул играбельных единиц |
| Мало играбельного контента (аудио только с правом использования, ADR 0014; кадры из TMDB требуют токен) | `seeds/media` с синтетикой для e2e; пул и `min_playable` в конфиге; `ScheduleDailySets` при нехватке ставит `failed`, пишет метрику и алерт; `503 game.daily_not_ready` с понятным экраном |
| Надписи на кадрах выдают ответ | Курация будущих наборов в админке (D6); флаг `playable` в catalog — этап 3 |
| Единицу заблокировали посреди дня | Раунд `void`, не входит в `max_score`; до блокировки набора единица заменяется автоматически |
| Отказ identity → нельзя обновить сервисный токен | Токен живёт 10 мин, обновление на 50 %, старый — до `exp`; identity ≥ 2 реплики; метрика `service_token_refresh_failures_total` |
| Две вкладки обновляют refresh одновременно → отзыв сессии | Web Locks single-flight на фронте; bff refresh не делает |
| Гонка двух стартов дейлика | Частичный unique-индекс + ответ существующей сессией |
| Периодические задачи в relay: relay упал — наборы дня не создаются | Ленивое создание набора при первом запросе дня (тот же use-case, unique `(mode_key, day)`); алерт на «нет набора на завтра» |
| Нет Docker в среде разработки | Kafka, OpenSearch и MinIO — только в CI; локально Postgres, Redis, RabbitMQ, moto; фронт — Playwright на моках; полный e2e — в CI |
| Фронтенд — новый стек в Python-монорепо | `gv test frontend`, отдельный CI-job, lock и инструменты внутри `frontend/`; конвенции — ADR 0017 |

## 12. Порядок слияния и проверки

1. **0a** `stage2/contracts` → `main`. Все потоки ветвятся от этого коммита.
2. **0b** `stage2/platform` (сервисные токены, `PeriodicJob`, purge, closers) — параллельно с потоками, вливается до B; после неё — `finecomb` по identity и `libs/auth`, полный `gv test`.
3. **A** (game_kernel, libs/text) — затем B и C делают rebase.
4. **B** (game) — component-тесты на реальных плагинах, затем живой прогон §0 п. 1, 3, 7 на compose в CI.
5. **C** (bff-web, гости) — после него — `finecomb` по diff identity.
6. **E** (admin) — нужны admin-API B и сервисный токен 0b.
7. **D** (фронтенд) — последним: e2e на полном стеке.

После каждого слияния оркестратор выполняет `uv lock --check`, `gv gen --check`, `gv contracts export && gv contracts check`, `gv test --changed`, а при изменениях `frontend/**` — `gv test frontend`. В конце: полный `gv test`, e2e `daily` в CI, живая демонстрация §0. Затем обновить `docs/09-roadmap.md` (итог этапа 2, новые хвосты), `docs/architecture/services.md` и `security.md`, `02-game-engine.md`, принять ADR 0016–0020.
