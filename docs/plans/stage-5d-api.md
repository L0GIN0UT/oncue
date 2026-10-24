# 5d: API и контракты (источник для потоков E1, E2, E3)

Ссылки: [план §12.1](stage-5.md), ADR [0054](../adr/0054-seasonal-events-model.md)–[0056](../adr/0056-event-rewards-via-ledger.md). Определение события — `contracts/progression/event_definition.v1.json`; события — `contracts/events/progression/{EventRunStarted,EventRunFinished,FindUnlocked,EventCompleted}.v1.json`. Источник истины по формам — снимки OpenAPI (`contracts/openapi/*.json`); здесь — смысл.

## progression, публичное `/v1` (`api/public/events.py`)
| Метод | Путь | Ответ |
|---|---|---|
| GET | `/v1/events/active` | анонимно; `{event: EventBanner \| null}`; `EventBanner = {run_id, key, title, theme_key, starts_at, ends_at}` |
| GET | `/v1/events/{key}` | вход; `EventPage`; запуск: активный, иначе последний `finished/stopped`; 404 `progression.event_not_found` (черновики и неопубликованные не видны) |
| GET | `/v1/me/events` | вход; архив: `{items: [EventArchiveItem]}` (запуски, где у игрока есть прогресс; новые первыми, ≤ 50) |
| POST | `/v1/events/{key}/finds/{find}/claim` | вход, CSRF, пустое тело; `ClaimResult`; идемпотентно; 404 `event_not_found`/`event_find_not_found`; 409 `event_not_active`/`event_find_not_ui_spot`/`event_find_locked`; 429 лимит (Redis, fail-closed 503) |

`EventPage = {key, title, description, theme_key, status: active|finished|stopped, starts_at, ends_at, map_url \| null, found, total, completed, reward_item: {item_key, title, image_url} \| null, finds: [EventFind]}`.
`EventFind = {key, number, title, x_pct, y_pct, final, state: locked|open|found, diary, reward: {xp, verses}, play_page \| null, trigger: {type: server|ui_spot, page_key \| null, metric \| null, progress \| null, target \| null, period \| null}, found_at \| null}`; `diary` — `diary_before` до открытия, `diary_after` после; у закрытой финальной находки (`locked`) прогресс триггера не отдаётся.
`ClaimResult = {find: EventFind, found, total, completed, newly_unlocked}`. `EventArchiveItem = {key, title, status, starts_at, ends_at, found, total, completed}`.

## progression, админ `/admin/v1/events` (`api/admin/events.py`)
Права `admin.access` + `progression.events.manage` (всё, кроме публикации и остановки) и `progression.events.publish` (публикация, остановка). Мутации — `Idempotency-Key` и `reason` (в `admin_actions`), успешные ответы `Cache-Control: no-store`.
| Метод | Путь | Смысл |
|---|---|---|
| GET | `/admin/v1/events` | список `{items: [AdminEventSummary{key, title, status, latest_run}]}` |
| POST | `/admin/v1/events` | `{key, body}` → 201 `AdminEvent`; 409 `event_key_taken`; 422 `event_definition_invalid` |
| GET | `/admin/v1/events/{key}` | `AdminEvent{key, body, body_sha256, map_url, runs: [AdminRun], updated_at}` |
| PUT | `/admin/v1/events/{key}` | `{body}`; 409 `event_definition_locked`, если есть запуск `published/active` |
| PUT | `/admin/v1/events/{key}/map` | multipart-картинка карты (статика PNG/WebP, лимиты — как `shop.media`); 422 `event_map_invalid` |
| POST | `/admin/v1/events/{key}/runs` | `{starts_at, ends_at}` → 201 `AdminRun` (draft) — это и перезапуск; 422 период |
| POST | `/admin/v1/events/{key}/runs/{run_id}/publish` | право `publish`; `{body_sha256}` (хеш определения, 409 `event_body_changed`); 409 `event_period_overlap`, `event_run_state_invalid`; кладёт снимок |
| POST | `/admin/v1/events/{key}/runs/{run_id}/stop` | право `publish`; активный/опубликованный → `stopped`; 409 `event_run_state_invalid` |
| GET | `/admin/v1/events/{key}/runs/{run_id}/stats` | `{run_id, players, completed, finds: [{find_key, number, unlocked}]}` |

`AdminRun = {id, seq, status, starts_at, ends_at, published_at, stopped_at}`; статус `active/finished` считается по времени.

## bff_web
- `/pages/shell`: `theme.key` = `theme_key` активного запуска (пусто → `default_theme_key`); новый блок `event: EventBannerCard \| null` (`{key, title, ends_at, found \| null, total \| null}`).
- `/pages/home`: блок `event: EventBannerCard \| null`.
- `/pages/events/{key}`: `EventPageView` = `EventPage` + `theme: {key}`; 404 при отсутствии.
- `/pages/profile`: блок `events: [EventArchiveItem]` (пустой список, если нет).
- Активное событие bff берёт из `GET /v1/events/active`, кэш ≤ 30 с, при недоступности progression — `default_theme_key` и `event: null` (деградация).

## Прочее
- Лимит claim: `GV_PROGRESSION__EVENT_CLAIM_LIMIT` (`<N>/<окно_с>` на игрока), задача relay `progression.events.tick` — `GV_PROGRESSION__EVENTS_TICK_INTERVAL_S`.
- Метрики реестра: `duel_win`, `weekly_top_place` (значение — лучшее место недели, находка засчитывается при месте ≤ `params.place_max`), `wheel_spin_streak` (уровень), `item_equipped` (по `ItemEquipped`), `collection_played` (сессия с `collection_id`; фильтра по тегу нет — хвост).
- Ключи ошибок — `progression.<код>`. Формы Problem — как у остальных ручек.
