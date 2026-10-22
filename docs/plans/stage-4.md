# План этапа 4: «Онлайн»

> Статус: **решения владельца приняты 2026-10-22** (раздел «Решения владельца» в конце; пункты с пометкой «по рекомендации» приняты по рекомендации планировщика). Основа: [roadmap](../09-roadmap.md) (этап 4), [концепция](../01-concept.md) §6, [движок](../02-game-engine.md) §8, [сервисы](../architecture/services.md) §5–6, [связи](../architecture/communication.md) §6, [масштабирование](../architecture/scalability-resilience.md) §2–3, [ADR 0010](../adr/0010-match-room-single-writer.md), [референс Songless](../reference/songless-reference.md) §8, §13, §15. Структура — как у [3a](stage-3a.md) и [3b](stage-3b.md).
> Ветка-база: `main` (fa113ef, этапы 0–3). Новые ADR: 0032–0040 (§2.9). Решения владельца по 4b от 2026-10-23 (в конце файла) имеют приоритет над текстом §0–§11 там, где они расходятся.

## Что уже есть

| Что | Состояние |
|---|---|
| `services/match`, `services/rating` | Заглушки этапа 0: health, метрики, outbox/inbox, миграция `0001_service_tables`, пустые слои. Домена нет |
| `libs/game_kernel` | `ModeEngine` (уровни, `draw_rounds`, `answer/check`, `score`, `clue_view`, `final_view`), плагины 2–3b; `SessionSpec.kind` — только `solo` (контракт `mode_config.v1.json`: `enum: ["solo"]`); `RoundState` — состояние одного игрока |
| Centrifugo v6 | В `compose.infra.yml` (Redis-движок, HMAC-секрет токенов, API-ключ), `location /connection/` на gateway. Ни один сервис его не вызывает, конфига namespace-ов нет |
| `gv_auth.Sanction` | Биты `RANKED`, `CHAT_SLOW`, `CHAT_MUTE` в claim `sanctions` уже читаются; события `UserSanctionsChanged` нет (этап 5) |
| game | Проекции каталога (алиасы, метрики), `clue_assets` по `params_hash`, сборники (`collection_proj`), конфиги режимов и их редактор в admin, выдача ссылок через media-pipeline (сервисный токен, scope `assets.link`) |
| `libs/service` | Процессы `api`, `relay`, `consumer`, `worker` (RabbitMQ), `all`; долгоживущего процесса-владельца комнат нет |
| Фронтенд | Реестры подачи `clue.kind` / `answer.format` / `result.presentation` (3a D12), single-flight refresh, CSP `connect-src 'self'` |

## 0. Цель и критерии готовности

**Цель.** Зарегистрированные игроки играют вместе в реальном времени (онлайн — только с аккаунтом). **4a:** хост создаёт комнату (дуэль 1×1 или party 3–6) по шаблону режима или по сборнику, зовёт друзей кодом или ссылкой, все отмечают готовность, матч идёт по таймерам сервера, состояние приходит через Centrifugo, отвалившийся игрок возвращается в тот же матч, падение инстанса match не роняет комнату. **4b:** рейтинговый подбор дуэли по ladder-ам `music`/`film` (очередь отдельная на формат «классика»/«лайт»), Эло с калибровкой, звания, сезоны, топ недели и лидерборды; формат `lite`. Ответ по-прежнему проверяет только сервер, клиент получает уровень загадки только после открытия уровня.

**Демонстрация 4a (локальный compose у владельца и CI):**
1. `gv dev seed` публикует онлайн-шаблоны `music.duel.classic`, `film.duel.frame`, `film.party.frame`, `music.lobby.classic`, `film.lobby.collection` (§2.4).
2. A создаёт кастомное лобби «кино по сборнику» (12 раундов, таймер 25 с, закрытое), B, C, D входят по коду. Смена настроек хостом сбрасывает готовность. Все готовы → отсчёт 3 с → матч.
3. Party на 4: очки `party_speed`, частичный ответ ×0,5; статусы игроков («Думает / Ответил / Мимо / Угадал») видны всем, тексты чужих ответов — никому.
4. C закрывает вкладку на 15 с: остальные видят «Отключён», C возвращается по ссылке, получает снимок с текущей фазой и `phase_ends_at` и продолжает. После `disconnect_grace_s` без возврата — выбывает, его очки сохраняются.
5. Дуэль A×B (`music.duel.classic`): 2 уровня, 3 попытки, опережение закрывает уровень для обоих; B сдаётся → «Соперник сдался»; реванш.
6. Во время матча убивают процесс-владелец комнаты: другой инстанс забирает lease, восстанавливает снимок, таймер пересчитывается по `phase_ends_at`, игроки видят разрыв ≤ lease TTL.
7. Centrifugo остановлен: клиент переходит на HTTP-tick, матч доигрывается.
8. Итог: место, очки, список единиц с «Смотреть позже» (library); уходит `match.MatchFinished`, валидный по схеме.
8а. Гость открывает `/online` или ссылку на комнату: экран «Онлайн доступен после регистрации» с кнопками входа и регистрации (возврат по `next`); API match отвечает гостю 403 `auth.guest_forbidden`.
8б. В кастомном лобби свободный чат: сообщение со словом из denylist отклоняется (`match.chat_rejected`); в дуэли доступны только эмоции и готовые фразы.

**Демонстрация 4b:**
9. Два клиента встают в очередь «Кино·Классика», матчер создаёт дуэль, после матча рейтинг меняется (калибровка: «осталось N матчей», звание скрыто), приходит `rating.RatingChanged`, итог показывает дельту.
10. Дуэль в «лайте» («Кино·Лайт», 4 варианта): дельта умножается на `format_multiplier` (0,8); выход или сдача — поражение по Эло и `leaver_penalty`. Party рейтинга не имеет.
11. Лидерборд ladder-а, топ недели (прирост с понедельника), блок «Сезон N · осталось X д.», звание с градацией и прогресс. Админ закрывает сезон: мягкий сброс 50 %, `SeasonFinished`.
12. Пользователь с санкцией `RANKED` получает 403 `match.ranked_forbidden` при входе в очередь.
13. Проходят `gv lint`, `gv test --changed` (все уровни), `gv contracts check`, `gv gen --check`, во `frontend/` — lint/typecheck/test/build и Playwright на моках, e2e `lobby`, `duel`, `party` (4a) и `ranked` (4b) на compose локально и в CI.

## 1. Сквозные решения

| # | Решение (рекомендация) | Варианты и почему |
|---|---|---|
| D1 | **Деление (решение 1):** 4a «Комнаты и матч» — комнаты, кастомные лобби, дуэль, party, realtime, реконнект; 4b «Рейтинг и подбор» — rating, матчмейкинг, сезоны, лидерборды, `lite`, наблюдение (§3) | Комнаты — фундамент, который показывается без рейтинга (лобби с друзьями); рейтинг и подбор опираются на `MatchFinished` и готовые комнаты |
| D2 | **Онлайн-режим — тот же `ModeConfig` в game**, `session` — дискриминированное объединение: `SoloSessionSpec` (как сейчас) \| `OnlineSessionSpec {kind: duel\|party\|lobby, players{min,max}, rounds, countdown_s, level_windows_s[], reveal_s, contest{type,params}, disconnect_grace_s, ranked, ladder?, lobby?{overridable}}`. game играет только `solo` (`game.mode_not_solo`), match — только онлайн. Поля `allow_guest` у онлайн-сессии нет (решение 4). Конфиги редактируются тем же редактором admin | Отдельные конфиги в match — второй редактор, второй валидатор, расхождение плагинов. Флаги `level_lock_on_first_correct` из §8 движка заменяются плагином `ContestRule` (D3) — по образцу `EndRule` (3a D3) |
| D3 | **Состязание — плагин ядра `ContestRule`:** `race` (дуэль: первый `exact` закрывает уровень для всех, следующий уровень открывается по таймеру; 3 попытки на уровень) и `parallel` (party: все отвечают в окне, раунд кончается по таймеру или когда ответили все). Многоигровое состояние раунда — `ContestRoundState` = общий уровень/фаза + `RoundState` на игрока; переходы чистые, время — аргумент `now_ms` | `if kind == "duel"` в match — хардкод. `RoundState` соло не меняется — регресс соло исключён |
| D4 | **Очки:** дуэль — `by_level` (`points: [12, 6]`), party — новый Scorer `party_speed` (`max(1, round(max_points × k × clamp(1 − t/T)))`, `k = partial_ratio`), `t` — от открытия уровня (сервер). Итог — `MatchOutcome` в ядре: места с ничьими, `win/loss/draw` для дуэли | Формула уже зафиксирована в §8 движка; property-тест `≥ 1` из `04-testing.md` |
| D5 | **Откуда match берёт раунды:** game — поставщик плана. При старте матча match один раз вызывает `POST /internal/v1/round-plans` (сервисный токен, scope `rounds.plan`): `{mode_key, version?, seed, rounds, collection_ids[], exclude_ids[]}` → тело конфига, `RoundDraw`-ы с ключами ответа (алиасы, partial-ключи), `asset_set_id` по уровням, `option`-ы для `pick`/`lite`. План хранится только в снимке комнаты и клиенту не уходит (решение 2). Своих проекций каталога, ассетов и сборников в match нет | Одна модель играбельности и одна проекция; вызов — один хоп и не на горячем пути. Цена: при падении game не стартуют новые матчи (идущие доигрываются) — строка `game` в таблице деградации меняется (§2.8) |
| D6 | **Комнаты — отдельный процесс `rooms`** (новый компонент `ServiceDefinition.build_runners` в `libs/service`; в compose входит в `all`, в k8s — свой Deployment с автоскейлом по числу комнат). `api` stateless: принимает команду, кладёт в Redis Stream комнаты `{room:CODE}:cmd`, ждёт ответ владельца `BLPOP {room:CODE}:reply:{op_id}` ≤ `GV_MATCH__REPLY_TIMEOUT_MS` (1500), иначе 202 `{accepted, version}` | Владелец в `api` — таймеры и lease живут в процессе, который масштабируется по RPS; SIGTERM на деплое API рвёт комнаты. Синхронный ответ нужен для `guess` (исход, попытки) |
| D7 | **Lease с fencing-эпохой** (уточняет ADR 0010): `{room:CODE}:owner = {instance, epoch}`, TTL 10 с, продление 3 с; захват увеличивает `epoch`. Запись снимка — Lua CAS «owner == me и epoch == мой», зомби-владелец получает отказ и бросает комнату. Снимок (`version`, `phase`, `phase_ends_at`, план, игроки, `op_id`-окно) — после каждого перехода; Redis с AOF уже включён. Graceful handoff по SIGTERM: перестать брать комнаты → снимок → `DEL owner` → другой инстанс подхватывает по сигналу из `rooms:orphans` | Без эпохи после паузы GC два владельца пишут один снимок. Ключи с hash-tag `{room:CODE}` — готовность к Redis Cluster |
| D8 | **Итог — в Postgres транзакцией с outbox:** владелец пишет `matches`, `match_players`, `match_rounds` и `MatchFinished` в одной транзакции; `UNIQUE(match_id)` делает повтор после handoff безопасным. Redis — горячее состояние, Postgres — история (партиционирование `match_rounds` по месяцу, data.md §4) | Снапшот Redis не даёт транзакции с outbox (как ADR 0019 для соло) |
| D9 | **Realtime:** connection-токен Centrifugo выдаёт identity (`POST /v1/realtime/token`, только пользователь, TTL 10 мин, `aud=centrifugo`); подписка на `room:{code}` — subscription-токен от match (`POST /v1/rooms/{code}/realtime-token`, только участник или наблюдатель с правом), отдельный секрет подписок. Публикует только бэкенд; namespace `room` — `history_size`/`history_ttl`, `force_recovery`, публикации клиентов запрещены. Сообщение — `{version, kind, delta}` (схемы `contracts/realtime/*`); пропуск версии → `GET /v1/rooms/{code}/state`. Личное (свои попытки, свои варианты `lite`) — только в ответе команды и в `state` по своему токену, в канал не идёт (по рекомендации) | Один HMAC-секрет на identity и match — компрометация match даёт подделку входа в любой канал. Канал на игрока `room_user:` — больше подписок без выигрыша: личного мало |
| D10 | **Присутствие и реконнект:** клиент шлёт `POST /v1/rooms/{code}/tick` раз в `GV_MATCH__TICK_S` (5 с; при отсутствии WebSocket — 1 с): это и heartbeat (`last_seen` в Redis-хэше комнаты, без владельца), и HTTP-фолбэк снимка. Владелец по `last_seen` переводит игрока в `disconnected`, через `disconnect_grace_s` — в `left`. Дуэль: пауза до `grace` → тех. победа. Party: матч идёт дальше. Вход в чужую активную комнату запрещён: `player:{id}:room` (409 `match.active_room_conflict`) | Centrifugo proxy-события присоединения требуют обратного HTTP из Centrifugo в match — лишний путь; tick нужен всё равно для деградации (services.md §5) |
| D11 | **Ассеты в матче:** ссылка уровня выдаётся одна на `(room, round, level)` в момент открытия уровня (match → media-pipeline, scope `assets.link`, субъект = комната), рассылается в дельте фазы. До открытия ссылки нет; ревил — финальный ассет только в фазе `reveal` | Ссылка на каждого игрока — N запросов в одну секунду и N записей журнала |
| D12 | **Кастомные лобби:** шаблон `*.lobby.*` с `lobby.overridable` (разрешённые значения `rounds`, `round_timer_s`, `answer.format`, `clue.type`, `players.max`, `visibility`), хост выбирает из них; источник `collection` принимает **несколько** `collection_ids` (расширение `DrawContext`, ADR 0028 уточняется). Память сыгранных единиц лобби (`exclude_ids`) + «Сбросить память». Код — 8 символов `[A-HJ-NP-Z2-9]`, `SET NX` | Произвольные числа от хоста — обход лимитов. Код без 0/O/1/I — меньше ошибок ввода |
| D13 | **Онлайн — только с аккаунтом (решение 4).** Все публичные ручки match (включая `GET /rooms/{code}`, вход по коду, `tick`, `state`, realtime-токены) — `require_user`; гость получает 403 `auth.guest_forbidden`. Фронт по claim `gst` показывает экран «Онлайн доступен после регистрации» (вход/регистрация с возвратом по безопасному `next` на комнату или хаб). bff отдаёт гостю `/pages/online` с блоком `{status: "auth_required"}` | Гость в комнате — сброс cookie обходит кики, лимиты и санкции; единая проверка проще, чем разрешения по шаблону |
| D14 | **Чат и эмоции (решение 5).** Везде — эмоции и готовые фразы из конфига (`chat.emotes[]`, `chat.phrases[]` — ключи i18n, без запаса и магазина до этапа 5). Свободный чат — только в кастомных лобби: та же проверка, что у UGC сборников 3b (`gv_text.ugc`: нормализация, затем denylist — ADR 0030, без своего словаря в match), ≤ 200 символов, без ссылок, лимит частоты, `CHAT_MUTE`/`CHAT_SLOW` из claim; отказ — 422 `match.chat_rejected`. Похвала и жалоба после матча → событие `PlayerRated` (обработка — этап 5) | Повторное использование denylist 3b: одна точка правки словаря и одна семантика нормализации |
| D15 | **Рейтинг (решения 4b, 2026-10-23; [ADR 0037](../adr/0037-rating-elo-calibration-ladder-config.md)):** только дуэль, классический Эло (K по калибровке: `calibration.matches` = 10, `k_calibrating`, `k`; старт 1000); ladder-ов два — `music` и `film` (ladder = домен), оба формата («классика», «лайт») идут в рейтинг, дельта «лайта» × `format_multiplier` (0,8); выход или сдача — поражение по Эло и `leaver_penalty` (−10), `aborted` по вине сервера рейтинг не меняет; звания с градацией (шаг 100), до калибровки скрыты. Ladder-ы, звания, сезоны, лидерборды — **конфиг rating** (`contracts/rating/ladder.v1.json`, версионируемый, YAML-сиды + admin-API), ключ ladder-а — в `session.ladder` режима; валидация ссылки — по проекции ключей в game (`rating.LadderPublished`). Party — без рейтинга | Классический Эло объясним игроку; конфиг ladder-ов в game — rating читал бы чужой конфиг |
| D16 | **Матчмейкинг ([ADR 0038](../adr/0038-matchmaking-queues.md)):** очередь = `(ladder, format)` в Redis ZSET по рейтингу (рейтинг общий на домен, игрок выбирает формат и стоит в одной очереди); батчевый матчер раз в `matchmaking.interval_s` в процессе `rooms` под lease очереди; окно ±100, каждые 10 с +50 до потолка (`matchmaking.window`), после `slow_hint_after_s` — «ищем дольше обычного», игрок остаётся в очереди. Подтверждение готовности не нужно — сразу комната с отсчётом; срыв отсчёта — без штрафа, соперник возвращается в очередь (риск принят); штрафов по времени и запрета очереди нет. Запись живёт по heartbeat (`POST /queue/tick`). Допуск — любой аккаунт без `RANKED`-санкции. Фильтры на старте выключены | Матчинг на каждом входе — гонки за одного игрока на нескольких инстансах |
| D17 | **Формат `lite`** (4 варианта, одна попытка) — `AnswerFormat` `pick` + новая возможность источника `DistractorSource`: game подбирает дистракторы из пула по близости (жанр, год, популярность — параметры в `answer.params.distractors`), `option_id` непрозрачны; подэтап 4b (по рекомендации) | Ручные неверные варианты в сборниках — объём collections, позже |
| D18 | **Сезоны и топы (решение 6; [ADR 0039](../adr/0039-seasons-weekly-top-leaderboards.md)):** длина и сброс — конфиг ladder-а `season {length_days: 56, soft_reset {target: 1000, ratio: 0.5}}` (новый рейтинг = `target + ratio × (old − target)`); сезон — запись rating (`starts_at`, `ends_at` по `length_days`), закрытие — периодическая задача по `ends_at` или админ, следующий сезон открывается автоматически; `SeasonFinished` с итоговыми местами, награды — progression на этапе 5. Топ недели — прирост с понедельника в зоне конфига, `WeeklyTopFinalized`. Лидерборды — Redis ZSET как хранилище чтения, пересобираются из Postgres задачей `rating.leaderboards.rebuild` | Без пересборки потеря Redis = потеря лидербордов (data.md §5 считает Redis кэшем, кроме match) |
| D19 | **Наблюдение и реванш:** реванш — в 4a (те же игроки, новая комната по согласию). Наблюдение — 4b, только с правом `match.spectate` (модератор, админ), без задержки; публичное наблюдение — после этапа 5 (по рекомендации) | Публичное наблюдение без задержки — подсказки игрокам через второй экран |

## 2. Шаг 0 — оркестратор (contract-first)

**0a** (ветки `stage4a/contracts`, затем `stage4b/contracts`) блокирует потоки своего подэтапа. **0b** (`stage4a/seed`, `stage4b/seed`) — засев и e2e, идёт параллельно с потоками.

### 2.1. Реестр, зависимости, CI
- `infra/services.toml`: новое поле `realtime` (CENTRIFUGO_API_URL/API_KEY/секрет подписок в env) — match, rating (4b), identity; правки шаблонов `tools/gv/src/gv_cli/templates/gen/*` + `gv gen`. Состав сервисов не меняется.
- `infra/centrifugo/config.json` (namespace-ы `room`, `personal`; `allow_publish_for_client: false`, история, recovery, лимиты соединений) и монтирование в `compose.infra.yml`; `location /connection/http_stream` на gateway (фолбэк-транспорт), лимит `ws_per_ip` пересмотреть.
- Зависимости: `cent` (Python-клиент server API, или тонкий httpx-адаптер — выбрать в 0a по `boring-by-default`), во фронте `centrifuge` (npm). `uv.lock` и `pnpm-lock.yaml` меняются только в 0a.
- `libs/service`: компонент `rooms` (`build_runners`, команда `match rooms`, graceful stop с handoff-хуком) — с тестами в `libs/service`.
- CI: job `e2e-smoke` + `e2e/{lobby,duel,party}.spec.ts` (4a), `e2e/ranked.spec.ts` (4b) под `E2E_SEEDED=1`; integration-тест handoff двух инстансов на реальном Redis.

### 2.2. События (новые топики)
- `match.match.v1`: `MatchStarted {match_id, version, room_code, mode_key, mode_version, domain, format, ranked, ladder|null, players[{user_id, slot}], started_at}`; `MatchFinished {…, players[{user_id, slot, score, place, outcome: win|loss|draw|null, finish: completed|forfeit|left|tech_win|tech_loss}], rounds[{index, media_item_id, results[{user_id, level, partial, score, elapsed_ms}]}], collection_ids[], aborted: bool}`; `PlayerRated {match_id, from_id, to_id, kind}` (4a хранит, потребитель — этап 5). Без ников и e-mail.
- `rating.rating.v1` (4b): `RatingChanged {user_id, ladder, format, season_id, match_id, version, old, new, delta, tier, old_tier, calibrating, calibration_left, matches_played, outcome, leaver_penalty, at}`, `LadderPublished {ladder, version, domain, formats, matchmaking, published_at}`, `SeasonStarted {season_id, ladder, number, ladder_version, starts_at, ends_at, soft_reset}`, `SeasonFinished {season_id, ladder, number, finished_at, closed_by, players_count, standings_ref}`, `WeeklyTopFinalized {ladder, week_start, top[{user_id, delta, place}]}`.
- Потребители: rating ← `match.MatchFinished`, `identity.UserDeleted`, `identity.UserProfileBasicsChanged` (ники лидербордов); match ← `identity.UserDeleted`, `UserProfileBasicsChanged` (ники в комнате), `rating.LadderPublished`; game ← `rating.LadderPublished` (валидация `session.ladder`).

### 2.3. `contracts/realtime/` (новый каталог)
- `room.message.v1.json` — конверт `{version, kind, server_now, delta}`; `kind ∈ {lobby, player, phase, level_opened, level_closed, round_reveal, summary, chat, emote, closed}`. Расширение `kind` — допустимое (клиент игнорирует неизвестные), удаление — новая версия. Проверка — `gv contracts check` (добавить каталог).

### 2.4. `contracts/modes/`
- `mode_config.v1.json`: `session` → `oneOf` по `kind` (solo без изменений — старые конфиги валидны); `OnlineSessionSpec` (D2); `answer.params.distractors?` (4b).
- `params/`: `contest.race.v1.json` (`attempts_per_level`, `lock_on_first_correct`), `contest.parallel.v1.json` (`end_when_all_answered`), `scorer.party_speed.v1.json` (`max_points`, `partial_ratio`), `lobby.overridable.v1.json`; `source.collection.v1.json` — `max_collections`.
- Сиды-шаблоны (§0 п. 1) — `seeds/game/modes/*.yaml`.

### 2.5. API (0a: схемы, заглушки 501, снимок OpenAPI)

| Сервис | Ручки |
|---|---|
| match `/v1` (4a) | `POST /rooms` (шаблон, переопределения, сборники), `GET /rooms/open?cursor`, `GET /rooms/{code}`, `POST /rooms/{code}/join\|leave\|ready\|settings\|kick\|transfer-host\|reset-memory\|start\|rematch`, `POST /rooms/{code}/realtime-token`, `GET /rooms/{code}/state`, `POST /rooms/{code}/tick`, `POST /rooms/{code}/match/guess\|skip\|forfeit\|emote\|chat\|away`, `POST /matches/{id}/rate`, `GET /matches/{id}`, `GET /me/matches?cursor` |
| match `/v1` (4b) | `POST /queue/enter\|leave`, `GET /queue/pool` (размер очередей), `GET /rooms/spectate?cursor` (право) |
| match `/admin/v1` | `GET /rooms` (активные), `POST /rooms/{code}/abort {reason}`, `GET /matches/{id}` |
| match `/internal/v1` | — |
| game `/internal/v1` | `POST /round-plans` (D5), `GET /modes/{key}@{version}` (тело конфига для валидации шаблона лобби) |
| identity `/v1` | `POST /realtime/token` (connection-токен, `require_same_origin`, CSRF) |
| rating `/v1` (4b) | `GET /ladders`, `GET /ladders/{key}/leaderboard?cursor\|around=me`, `GET /ladders/{key}/weekly`, `GET /me/ratings`, `GET /users/{id}/ratings`, `GET /me/ratings/{ladder}/history?cursor`, `GET /matches/{id}/changes` (дельты матча для итога) |
| rating `/admin/v1` (4b) | ladder-ы (черновик, публикация, откат), сезоны (создать, закрыть), пересборка лидерборда |
| bff-web `/api/web/v1` | `/pages/online` (4a: открытые лобби, мои матчи; 4b: очереди, сезон, топ недели), `/pages/leaderboards` (4b), `/pages/match/{id}` |

Все мутации — `Idempotency-Key` (`op_id` в теле команды комнаты), ошибки RFC 9457 (`match.*`, `rating.*`), курсорная пагинация.

### 2.6. Ядро — сигнатуры (0a, `libs/game_kernel`)
- `spec.py`: `OnlineSessionSpec`, `ContestSpec`, `LobbySpec`; `SessionSpec = Solo | Online` (дискриминатор `kind`).
- `plugins.py`: протоколы `ContestRule` (`open_level`, `apply_guess`, `level_closed`, `round_finished`), `DistractorSource` (4b); `contest.py`: `ContestRoundState`, `PlayerRoundView`; `outcome.py`: `MatchOutcome.rank(scores) → placements`.
- `engine.py`: `ModeEngine.contest_*` фасад; `DrawContext.collection_ids` (старое `collection_id` читается как список из одного).
- `gv_testkit/match.py`: DSL `given_match(cfg).with_players(...).at(t).when_guess(player, ...).then_level_closed().then_scores(...)`, `FakeRoundPlanPort`, `FakeRealtime`, `FakeLinkPort`.

### 2.7. Права и настройки
- identity `0008_seed_online_perms`: `match.spectate`, `match.manage` (admin-API комнат), `rating.ladders.publish`, `rating.seasons.manage`; клиенты сервисных токенов `match → game (rounds.plan)`, `match → media_pipeline (assets.link)`.
- Настройки match (`GV_MATCH__*`): `LEASE_TTL_S=10`, `LEASE_RENEW_S=3`, `REPLY_TIMEOUT_MS=1500`, `TICK_S=5`, `MAX_ROOMS_PER_INSTANCE`, `LOBBY_CREATE_PER_USER_PER_HOUR`, `JOIN_BY_CODE_PER_IP_PER_MIN`, `COMMANDS_PER_PLAYER_PER_S`, `CHAT_PER_PLAYER_PER_MIN`, `ROOM_IDLE_CLOSE_MIN`. Игровые величины (таймеры, очки, раунды) — только в конфиге режима.
- rating (`GV_RATING__*`): `LEADERBOARD_PAGE_CACHE_S=10`, `REBUILD_INTERVAL_S`, `WEEK_TIMEZONE`.

### 2.8. Инфраструктура и деградация
- Таблица деградации (`scalability-resilience.md` §4.2): строка `game` — «онлайн: идущие матчи доигрываются, новые не стартуют» (D5); строка `Centrifugo` уже верна.
- Метрики: `gv_match_rooms_active`, `gv_match_command_apply_seconds`, `gv_match_lease_takeovers_total`, `gv_match_queue_wait_seconds{ladder}`, `gv_rating_consumer_lag`; дашборд Grafana «Онлайн».
- Целевые числа этапа (допущение, проверяется нагрузкой на этапе 7): p95 «команда → дельта в канале» < 150 мс, ≥ 300 активных комнат на инстанс `rooms` при 320 МБ.

### 2.9. ADR и документация
| ADR | Тема | Подэтап |
|---|---|---|
| 0032 | Онлайн-сессии в `ModeConfig`: `OnlineSessionSpec`, плагин `ContestRule`, `party_speed`, шаблоны лобби и `overridable` (уточняет 0008, 0023) | 4a |
| 0033 | Источник раундов матча — план от game по internal API, без проекций в match; изменение деградации | 4a |
| 0034 | Процесс `rooms`: протокол команд через Redis Stream с ответом, fencing-эпоха lease, снимки, handoff, итог в Postgres + outbox (уточняет 0010) | 4a |
| 0035 | Realtime: токены Centrifugo (connection — identity, subscription — match), namespace-ы, версии и recovery, HTTP-tick, личные данные вне канала | 4a |
| 0036 | Онлайн только с аккаунтом; эмоции и фразы везде, свободный чат в лобби через denylist ADR 0030; оценки игроков до модерации | 4a |
| 0037 | Рейтинг: попарный Эло, калибровка, ladder-ы и звания — конфиг rating, `LadderPublished` | 4b |
| 0038 | Матчмейкинг: очереди Redis, батчевый матчер под lease, окно Эло, штрафы | 4b |
| 0039 | Сезоны (8 недель, мягкий сброс 50 % к 1000 — конфиг), топ недели, лидерборды: ZSET как хранилище чтения с пересборкой | 4b |
| 0040 | Формат `lite`: дистракторы — возможность источника (`DistractorSource`) | 4b |

Документация: `02-game-engine.md` §8 (переписать по D2–D4), `services.md` §5–6 (данные, события, процесс `rooms`), `communication.md` §6, `scalability-resilience.md` §2, §4.2, CLAUDE.md match и rating («Особенности»), `09-roadmap.md`.

## 3. Потоки и владение файлами

### 4a «Комнаты и матч»
| Поток | Ветка | Может менять |
|---|---|---|
| **A** ядро | `stage4a/kernel` | `libs/game_kernel/**` (кроме сигнатур 0a), `libs/testkit/src/gv_testkit/{game,match}.py` |
| **R** match | `stage4a/match` | `services/match/**`, `contracts/openapi/match.json` |
| **B** game | `stage4a/game` | `services/game/**`, `contracts/openapi/game.json`, `seeds/game/**` |
| **I** identity | `stage4a/identity` | `services/identity/**` (realtime-токен), `contracts/openapi/identity.json` |
| **C** bff | `stage4a/bff` | `services/bff_web/**`, `contracts/openapi/bff_web.json` |
| **D** фронтенд | `stage4a/web` | `frontend/**` |
| **E** admin | `stage4a/admin` | `services/admin/**` |
| Оркестратор | `stage4a/contracts`, `stage4a/seed` | `contracts/{events,modes,realtime}/**`, `libs/{service,core,testkit}` (кроме `game.py`, `match.py`), `services/identity/**/0008_*`, `tools/gv/**`, `infra/**` (через `gv gen`), `e2e/**`, `.github/**`, `docs/**` |

### 4b «Рейтинг и подбор»
| Поток | Ветка | Может менять |
|---|---|---|
| **A2** ядро | `stage4b/kernel` | `libs/game_kernel/**` (`DistractorSource`, валидация `ranked`/`ladder`) |
| **G** rating | `stage4b/rating` | `services/rating/**`, `contracts/openapi/rating.json` |
| **Q** match: очереди и ranked | `stage4b/match` | `services/match/**` |
| **B2** game | `stage4b/game` | `services/game/**` (дистракторы, проекция ladder-ов), `seeds/game/**` |
| **C2**, **D2**, **E2** | `stage4b/{bff,web,admin}` | как в 4a |
| Оркестратор | `stage4b/contracts`, `stage4b/seed` | `contracts/events/rating/**`, `seeds/rating/**`, остальное как в 4a |

**Параллелизация.** 4a: после 0a параллельно A, B, I, R (R — на фейках `gv_testkit.match` до слияния A), C; D — на моках по снимку OpenAPI и `contracts/realtime`; E — после R. 4b: G стартует сразу после 0a 4b (зависит только от схемы `MatchFinished`) и может идти параллельно с хвостом 4a; Q — после G-контрактов; A2/B2 (`lite`) независимы от G/Q.

## 4. Поток A — ядро (4a)

| # | Файлы (`libs/game_kernel/src/gv_game_kernel/...`) | Действие | Тест | Риск |
|---|---|---|---|---|
| A1 | `spec.py`, `registry.py` | `OnlineSessionSpec`; `validate`: `players.min ≤ max ≤ 6` (лобби — ≤ `players.max` контракта), `len(level_windows_s) == число уровней`, совместимость `contest` ↔ `answer.format` (`race` — `classic`; `parallel` — `classic`/`pick`), `ranked ⇒ ladder`, у онлайн-сессии нет `allow_guest` (ошибка `schema.*`), `overridable` ⊆ допустимых диапазонов контракта; соло-конфиги без изменений | «сломанный конфиг на каждое правило»; все сиды 2–3b валидны | средний |
| A2 | `contest.py`, `builtin/contests.py` | `race`, `parallel`: открытие уровня, `apply_guess(player, now_ms)`, закрытие по `exact` (race) / таймеру / «все ответили», `void` для вышедшего | unit + property: попытки ≤ `attempts_per_level`, после закрытия уровня ход → `LevelLockedError`, порядок ходов в одну миллисекунду детерминирован (по порядку в стриме) | высокий |
| A3 | `builtin/scorers.py` | `party_speed` | property: `≥ 1`, монотонно убывает по `t`, partial = `round(× ratio)` | низкий |
| A4 | `outcome.py` | `MatchOutcome.rank`: места с ничьими (`1, 1, 3`), `win/loss/draw` для 2 игроков, вышедшие — после оставшихся | unit на таблице случаев | низкий |
| A5 | `engine.py`, `builtin/sources.py` | `contest_*` фасад; `collection_ids` в `DrawContext` и `CollectionSource` (порядок: чередование сборников, без повторов) | регресс 3b (один сборник) | средний |
| A6 | `gv_testkit/match.py` | DSL и фейки (§2.6) | — | низкий |

**Готово, когда** `gv test libs/game_kernel` зелёный (≥ 90 %), сиды 2–3b проходят без изменений, golden matcher не меняется.

## 5. Поток R — match (4a, основной объём)

| # | Файлы (`services/match/src/match_service/...`) | Действие | Тест | Риск |
|---|---|---|---|---|
| R1 | `domain/room.py` | Агрегат лобби: хост, слоты, готовность (сброс при смене настроек), `kick` (возврат только по коду), `transfer_host` (авто при уходе хоста), видимость, память сыгранного | unit: инварианты, хост ушёл, кик хоста запрещён | средний |
| R2 | `domain/match_fsm.py` | Автомат `lobby → countdown → level_open → level_closed → reveal → … → summary → closed` + `paused` (дуэль, отключение) + исходы `completed / forfeit / tech_win / aborted`; все переходы — по командам и дедлайнам `phase_ends_at`, время через `Clock` | unit с `FakeClock`: таймауты фаз, опережение, сдача, тех. победа, реконнект до/после `grace`, повтор `op_id`, команда старой фазы → 409 `match.phase_mismatch` | высокий |
| R3 | `application/commands/*` | `create/join/leave/ready/settings/kick/start/rematch`, `guess/skip/forfeit/emote/chat/away`; проверка участия и прав в application; старт: вызов `RoundPlanPort` (game), план — в снимок | component: каждый сценарий §0 4a | высокий |
| R4 | `infrastructure/rooms/{lease,stream,snapshot,runner}.py` | D6–D7: lease с эпохой (Lua), чтение стрима владельцем, таймеры, Lua-CAS снимка, handoff по SIGTERM, подбор сирот, лимит комнат на инстанс | integration (реальный Redis): два runner-а, kill одного, захват ≤ TTL; зомби с устаревшей эпохой не пишет; SIGTERM — handoff без потери команд | высокий |
| R5 | `infrastructure/realtime.py` | Публикация дельт в Centrifugo server API (таймаут, без ретраев с повтором версии — клиент ресинкнет), subscription-токены | component: дельты по версиям без пропусков; `FakeRealtime` | средний |
| R6 | `infrastructure/{game_client,media_client}.py` | D5 и D11: сервисные токены, таймауты, circuit breaker; ошибка плана → `aborted` с причиной (`match.pool_too_small`, `match.content_unavailable`), без висящей комнаты | component на заглушках `gv_testkit.stubs` | средний |
| R7 | `infrastructure/db/migrations/versions/0002_match_core.py`, `db/tables.py` | `matches` (`UNIQUE(match_id)`), `match_players`, `match_rounds` (партиции по месяцу + задача создания партиций вперёд), `player_ratings_given` (оценки), `users_proj (user_id, nickname)`, `idempotency_keys` | integration: миграция вверх/вниз, CHECK-и, повторная запись итога — no-op | средний |
| R8 | `infrastructure/messaging/*` | outbox `MatchStarted/Finished/PlayerRated`; consumers `UserDeleted` (обезличить историю, выкинуть из комнат), `UserProfileBasicsChanged` | integration: идемпотентность по inbox | низкий |
| R9 | `api/public/*`, `api/admin/*` | Ручки §2.5; `tick` и `state` читают снимок без владельца и **фильтруют** его по зрителю (`RoomView.for_player`) | contract + component; античит-тест §12 | высокий |

## 6. Поток B — game (4a)

| # | Что | Тест | Риск |
|---|---|---|---|
| B1 | Валидация онлайн-конфигов при публикации (ядро A1), запрет старта онлайн-режима в соло (`game.mode_not_solo`), выдача онлайн-режимов в `GET /v1/modes?session=online` | component | низкий |
| B2 | `POST /internal/v1/round-plans` (D5): розыгрыш через `ModeEngine.draw_rounds` с `exclude_ids` и `collection_ids`, ключи ответа (алиасы, partial), `asset_set_id` по уровням, проверка играбельности; только сервисный токен match | component: план детерминирован по seed; пользовательский JWT → 401; сборник скрыт → 404 | средний |
| B3 | Сиды онлайн-шаблонов (§0 п. 1), bootstrap публикует их | — | низкий |

## 7. Поток I — identity (4a, зона повышенной безопасности)

| # | Что | Тест | Риск |
|---|---|---|---|
| I1 | `POST /v1/realtime/token`: connection-JWT Centrifugo (`sub`, `exp` ≤ 10 мин, `aud`, без `perms`/`channels`), только `require_user` (гость → 403); `require_same_origin`, CSRF, лимит частоты | component: без сессии 401, гость 403, отозванный `sid` 401, токен не принимается как access-токен сервисов (другой `aud`/ключ) | средний |
| I2 | `0008_seed_online_perms`, клиенты сервисных токенов | integration | низкий |

Ревью уровня §3 `10-ai-assistant.md`: security-reviewer + `finecomb` по diff; в отчёте — «требуется ручная проверка владельцем проекта».

## 8. Поток C — bff-web (4a / 4b)
- 4a: `/pages/online` (открытые лобби, мои последние матчи), `/pages/match/{id}` (итог + карточки единиц из catalog); деградация по блокам, как в ADR 0017.
- 4b: блоки очередей, сезона и топа недели, `/pages/leaderboards`; кэш публичных лидербордов 10 с.

## 9. Поток D — фронтенд (4a / 4b)

| # | Что | Тест |
|---|---|---|
| D1 | `shared/realtime`: клиент `centrifuge`, токены (single-flight, как refresh), подписка комнаты, контроль `version` → ресинк, баннер «нет связи» после 20 с, переход на tick, поправка часов `server_now` | unit (vitest) на последовательностях версий и обрывах |
| D2 | `/online` (хаб), `/lobbies` (список, вход по коду), `/room/[code]` (лобби: игроки, готовность, настройки хоста; матч: загадка через `clueRegistry`, ответ через реестр форматов, таймер по `phase_ends_at`, статусы игроков, эмоции, чат по D14; итог: места, очки, «Смотреть позже», реванш, оценка); экран «Онлайн доступен после регистрации» для гостя на всех онлайн-маршрутах | Playwright на моках: 2–4 контекста, гость видит экран регистрации |
| D3 | `/match/[id]`, «Мои матчи» в `/me` | Playwright |
| D4 (4b) | Очередь (кнопки ladder-ов, ожидание, сигнал и мигание заголовка), `/leaderboards`, звание и сезон в хабе и профиле, дельта рейтинга в итоге (`/matches/{id}/changes` опросом до 10 с) | Playwright |
| D5 | CSP: явный `wss://` своего хоста в `connect-src` (`'self'` для WebSocket не везде трактуется одинаково) — правка `proxy.ts` с тестом | `proxy.test.ts` |

## 10. Поток E — admin (4a / 4b)
- 4a: раздел «Матчи» — активные комнаты (admin-API match), принудительное завершение с `reason`, просмотр итога; онлайн-шаблоны в существующем редакторе конфигов (подсказки по `OnlineSessionSpec`).
- 4b: раздел «Рейтинг» — ladder-ы (YAML, diff, публикация, откат), сезоны (создать, закрыть), пересборка лидерборда; аудит всех действий.

## 11. 4b: потоки G (rating), Q (match), A2/B2

| # | Что | Тест | Риск |
|---|---|---|---|
| G1 | Домен: `Ladder` (конфиг с версией по `contracts/rating/ladder.v1.json`), `Rating(user, ladder, season)`, `EloCalculator` (D15) | property: сумма дельт дуэли = 0 при равном K и множителе 1, победа над сильным ≥ над слабым, калибровка и `format_multiplier`; golden-таблица | средний |
| G2 | Миграция `0002_rating_core`: `ladders`, `ladder_versions`, `seasons`, `ratings` (PK `(user_id, ladder, season_id)`), `rating_history` (партиции по сезону), `weekly_deltas`, `processed_matches` (дедупликация по `match_id` поверх inbox), `users_proj` | integration | средний |
| G3 | Consumer `MatchFinished`: только `ranked`; `aborted` и матчи с выходом до N-го раунда — по правилу ladder-а; одна транзакция «рейтинги + history + outbox `RatingChanged`» | integration: повтор события — no-op; порядок событий по одному игроку | средний |
| G4 | Лидерборды ZSET (`{lb:ladder:season}`), `around=me`, пересборка из Postgres, скрытие удалённых | integration | низкий |
| G5 | Периодические задачи: закрытие сезона по `ends_at`, мягкий сброс, `WeeklyTopFinalized` по понедельникам (advisory lock, идемпотентно по периоду) | component с `FakeClock` | средний |
| Q1 | Очереди и матчер (D16) в процессе `rooms` под lease очереди; создание ranked-комнаты сразу в `countdown` | integration: два матчера — пара не матчится дважды; расширение окна во времени | высокий |
| Q2 | Ranked-правила: `Sanction.RANKED` → 403, выход/сдача = поражение (+`leaver_penalty` в rating), срыв отсчёта без штрафа, рейтинг для очереди — из проекции `RatingChanged` в match (без синхронного вызова rating) | component | средний |
| Q3 | Наблюдение (D19): subscription-токен с правом `match.spectate`, вид без личных данных | component, античит | средний |
| A2/B2 | `lite`: `DistractorSource` в ядре, подбор в game (`round-plans` отдаёт `options`), формат `pick` в `parallel` | unit + component: правильный вариант не выводится из `option_id` и порядка | средний |

## 12. Обязательные проверки безопасности
1. **Античит:** до `reveal` в `state`, `tick`, ответах команд и сообщениях канала нет `media_item_id`, названия, ключей ответа и плана (рекурсивный обход JSON, как в 3a); ссылка уровня — только после открытия уровня; тексты ответов соперников не уходят никому, только статус; `option_id` непрозрачны и перемешаны на комнату; время и скорость — только сервер.
2. **Авторизация:** все публичные ручки match и realtime-токены — `require_user` (тест «гость → 403» на каждую ручку); команды комнаты — только участник своего слота; настройки, кик, старт — только хост (проверка в application); `state` фильтруется по зрителю; наблюдение — право `match.spectate`; subscription-токен — только участнику; чужой матч в истории — по правилу видимости (участники + публичные итоги без личных данных).
3. **Целостность:** `op_id` у каждой команды (окно дедупликации в снимке), `expected_version` для команд фазы, single-writer + fencing-эпоха, итог — `UNIQUE(match_id)` + outbox; rating — дедупликация по `match_id`.
4. **Лимиты:** создание лобби, вход по коду (перебор кодов — по IP и пользователю, fail-closed), команды и чат на игрока, токены realtime; Centrifugo — лимит соединений на пользователя.
5. **Centrifugo:** клиенты не публикуют, namespace-ы без анонимного доступа, отдельный секрет подписок, TTL токенов, API-ключ только у сервисов с `realtime = true`.
6. **UGC:** чат и ник — `gv_text.ugc`, экранирование на фронте, `CHAT_MUTE`/`CHAT_SLOW` из claim.
7. **Ranked:** санкция `RANKED`, win-trading (повтор пар), dodging.
8. **identity (realtime-токен):** зона повышенной безопасности — security-reviewer, `finecomb`, ручная проверка владельцем.
9. **Процедура:** R, Q, G, B, I, E — `security-discipline`; новые OpenAPI — `42crunch-audit`; R4, R7, G3, Q1 — `idempotency-and-side-effects`; миграции — `review-migration`; лидерборды и история — `explain-this-query`.

## 13. Миграции и совместимость
- Только расширяющие миграции: match `0002`, rating `0002` (4b), identity `0008`; game — без миграций в 4a (проекция ladder-ов `0006` в 4b).
- Контракт конфига: `session` — `oneOf` с прежней solo-веткой без изменений; сиды и сохранённые конфиги 2–3b валидны (регресс-тест на всех `seeds/game/modes/*.yaml`). `gv contracts check` должен признать `oneOf` совместимым — согласовать в 0a.
- `DrawContext.collection_id` → `collection_ids`: старое поле читается, сессии 3b не затронуты.
- Порядок выкладки: game (валидатор и `round-plans`) → identity (токен) → match (продюсер `MatchFinished`) → rating (4b, потребитель догоняет по логу Redpanda с начала топика).
- Снимок комнаты имеет `schema_version`; захват комнаты инстансом новой версии читает предыдущую (тест на сохранённом снимке) — иначе деплой посреди матча ломает комнату.

## 14. Что НЕ входит
- Лобби до 32 игроков (в концепции — опция; платная в референсе, биллинг выключен), турниры, стримерский режим, Telegram Mini App — после релиза.
- Награды за матчи, сезоны и топ недели (валюта, бейджи), запас эмоций и магазин — этап 5 (progression потребляет `MatchFinished`, `SeasonFinished`, `WeeklyTopFinalized`).
- Приглашения друзей и статусы «На сайте» (social), уведомления «вас пригласили» (notifications) — этап 5; в 4a — только код и ссылка.
- Обработка жалоб и похвал, шкалы поведения, `UserSanctionsChanged` — этап 5 (в 4a санкции — из claim JWT).
- Боты для пустых очередей; детектор аномалий; публичное наблюдение (кастеры); лидерборды уровней, серий и прогнозов — этап 5.
- Свои неверные варианты `lite` в сборниках; смешанные (музыка + кино) лобби.

## 15. Риски

| Риск | Мера |
|---|---|
| Lease/handoff: двойной владелец, потеря команд при переходе | Fencing-эпоха и CAS снимка (D7), integration-тест kill/SIGTERM, команды в стриме не удаляются до подтверждения применения (`XACK` после снимка) |
| Порядок и задержка команд через Stream + ответ | Метрика `command_apply_seconds`, таймаут ответа с 202, нагрузочный тест комнаты на 6 игроков × частые ответы в component |
| Расхождение realtime и состояния | Версия в каждом сообщении, ресинк по REST, tick как вторая гарантия (как в референсе) |
| Деплой посреди матча | Graceful handoff, `schema_version` снимка, совместимость N/N−1 |
| Падение game блокирует старт матчей (D5) | Circuit breaker, понятная ошибка «онлайн временно недоступен», идущие матчи не зависят от game |
| Пустые очереди при малой аудитории | Без фильтров на старте (D16), счётчик очереди в хабе, кастомные лобби как основной путь 4a |
| Многопользовательский Эло непонятен игроку | Попарная модель с объяснимой дельтой; калибровка скрывает звание до N матчей |
| Объём (два сервиса с нуля) | Деление 4a/4b; `lite`, наблюдение и чат выносятся без влияния на остальное |
| Centrifugo — первое реальное использование | Ранний spike в 0a (токены, recovery, история) на compose до потоков |

## 16. Порядок слияния
**4a:** 0a `stage4a/contracts` (контракты, `libs/service` `rooms`, `services.toml`, Centrifugo-конфиг, identity `0008`, ADR 0032–0036) → **A** → **B** и **I** → **R** (rebase на A; component на реальных плагинах) → 0b засев и `e2e/{lobby,duel,party}.spec.ts` → **C** → **E** → **D** последним, живая демонстрация §0 п. 1–8.
**4b:** 0a `stage4b/contracts` (события rating, ladder-сиды, ADR 0037–0040) → **A2/B2** (`lite`) → **G** → **Q** → 0b `e2e/ranked.spec.ts` → **C2** → **E2** → **D2**, демонстрация п. 9–13.

После каждого слияния: `uv lock --check`, `gv gen --check`, `gv contracts export && gv contracts check`, `gv test --changed`, `gv test frontend`. В конце подэтапа — обновить `09-roadmap.md`, принять ADR.

---

## Решения владельца (2026-10-22)

1. **Деление (D1):** 4a — комнаты, кастомные лобби, дуэль, party, realtime, реконнект; 4b — rating, матчмейкинг, сезоны, лидерборды.
2. **Раунды (D5):** match получает план раундов от game по internal API `POST /internal/v1/round-plans` при старте матча; своих проекций каталога, ассетов и сборников в match нет.
3. **Рейтинг (D15):** попарный Эло с калибровкой.
4. **Гости (D13):** онлайн только с аккаунтом — гостю недоступны ни лобби, ни вход по коду; фронт показывает экран «Онлайн доступен после регистрации».
5. **Чат (D14):** эмоции и готовые фразы везде; свободный чат только в кастомных лобби, через denylist этапа 3b (`gv_text.ugc`, ADR 0030).
6. **Сезоны (D18):** 8 недель, мягкий сброс 50 % к 1000; длина и сброс — конфиг ladder-а; награды — этап 5.
7. **По рекомендации планировщика:**
   - `lite` (D17) — в 4b, дистракторы автоматически из пула;
   - фильтры подбора (D16) — выключены (очередь на ladder и формат);
   - токены Centrifugo (D9) — connection от identity, subscription комнаты от match, разные секреты;
   - наблюдение (D19) — 4b, только `match.spectate` (модераторы, админы); реванш — 4a;
   - остальные решения таблицы §1 (D2–D4, D6–D8, D10–D12) — как записаны.

## Решения владельца по 4b (2026-10-23)

Приоритетнее остального текста плана; ADR [0037](../adr/0037-rating-elo-calibration-ladder-config.md)–[0040](../adr/0040-lite-format-distractor-source.md).

1. **Ladder-ов два** — `music` и `film` (ladder = домен); поправлены §1 D16, §11 G1.
2. **Рейтинг только у дуэли.** Party — без рейтинга, многопользовательский Эло не делается.
3. **Оба формата в рейтинге:** «классика» и «лайт»; рейтинг общий на домен, очередь отдельная на формат; дельта «лайта» × `format_multiplier` = 0,8 (конфиг).
4. **Подбор:** окно ±100, каждые 10 с +50, потолок; «ищем дольше обычного», игрок остаётся в очереди. Всё в конфиге ladder-а.
5. **Эло:** старт 1000, калибровка 10 матчей с повышенным K, дальше обычный K; стандартная формула (победа над сильным даёт больше, поражение от сильного снимает меньше). В калибровке звание скрыто, показывается «осталось N матчей».
6. **Звания** с градацией, шаг 100: Бронза 1–3 (<1000/1100/1200), Серебро 1–3 (до 1500), Золото 1–3, Платина 1–3, Алмаз ≥ 2100 без градации. Пороги и названия — в конфиге ladder-а.
7. **Штрафов по времени нет.** Выход или сдача в рейтинговом матче — поражение по Эло и `leaver_penalty` = −10 (конфиг). Срыв отсчёта — без штрафа. `aborted` по вине сервера — рейтинг не меняется.
8. **Допуск:** любой зарегистрированный аккаунт, без дополнительных условий (кроме санкции `RANKED`).
9. **Сезон** 8 недель, мягкий сброс 50 % к 1000. Косметика и награды — этап 5.
