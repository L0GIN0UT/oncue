# bff_web

Backend-for-frontend: агрегация данных под экраны веб-клиента.

- Пакет: `bff_web_service`; запуск: `bff_web api`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: `/api/web/v1/...` на gateway (только GET)
- Зависимости: Redis (кэш); вызывает game, identity, catalog, collections, library, match по HTTP
- Статус: этап 2 — экраны session, home, solo, results (`docs/plans/stage-2.md` §6); этап 3a — витрина режимов, недельная сетка, рекорды, итог по `presentation` (`docs/plans/stage-3a.md` §7); этап 3b — каталог и страница сборника, медиатека (`docs/plans/stage-3b.md` §9); этап 4a — хаб онлайна и итог матча (`docs/plans/stage-4.md` §8)
- Граф зависимостей собирает `wiring.py` (вне слоёв); `api/deps.py` — что получают роутеры.

## Особенности
- Только чтение (D4): мутаций нет, CSRF и идемпотентность не нужны; команды игры браузер шлёт прямо в game.
- Схемы экранов — `api/public/schemas.py`; блок упавшего источника — `{"status": "unavailable"}`, страница всё равно 200.
- Токен — из cookie `gv_access` или Bearer, дальше передаётся как Bearer; недействительный токен в `/v1/session` — `subject: null`, не 500.
- Публичные ответы — `public, max-age=30`, личные — `private, no-store`; личные блоки не кэшируются.
- Токен проверяется локально (JWKS identity, `GV_JWKS_URL`): недействительный → аноним (блок дейликов `unavailable`, game не вызывается); JWKS недоступен → токен уходит источникам непроверенным. Вид субъекта — по claim `gst`, имя — из identity `/v1/me` (гостю он отвечает 403).
- Источники: `GV_UPSTREAMS__{GAME,CATALOG,IDENTITY,COLLECTIONS,LIBRARY,MATCH}_URL` (по умолчанию — DNS compose), общий таймаут `timeout_s` (1,5 с), лимит параллельных вызовов на запрос, circuit breaker на сервис (`breaker_failures`/`breaker_reset_s`); 5xx/429/таймаут/ответ вне контракта → блок `unavailable`, 4xx → решает use-case. Редиректам не следуем (Bearer не уходит по чужому адресу).
- Кэш Redis (`GV_PAGE_CACHE__*`): только публичные `game /v1/modes` (и `?session=online` под своим ключом) и `/v1/collections/{id}/modes` (30 с), витрины каталога сборников без `q`/курсора (30 с) и карточки catalog (5 мин); в кэш попадает тело, прошедшее разбор; ошибки Redis — промах. Всем страницам — `Vary: Authorization, Cookie`.
- Раскладка по блокам — по данным game, без ключей режимов (`PageLayout`, `GV_PAGES__*`): `random_source_types` → блок `random`; `scheduled_source_types` (`daily`, `bracket`) не попадают в витрину `modes`, `collection_source_types` (`collection`) — ни в одну витрину (game их в `/v1/modes` и так не отдаёт); статусы `/v1/daily` режимов `weekly_source_types` (`bracket`) идут в блок `weekly`, остальные — в дейлики (на главной и в хабе). Витрина `modes` — группы `(source_type, presentation)` в порядке ответа game.
- `records` (хаб) — личный блок из `game /v1/me/records`, только пользователю: гостю и анониму `records: null`, game не вызывается. Рекорды и карточки периода без опубликованного режима пропускаются; без `/v1/modes` такие блоки `unavailable`.
- Каталог сборников (`/v1/pages/collections`): collections зовётся без токена; без `q` и `cursor` — витрины `featured`/`popular`/`new` (сортировка = имя блока, общие фильтры, `next_cursor` на «показать все»), иначе только `results`; с `q` — `private, no-store`. Отказ collections 4xx (чужой курсор) — ответ экрана.
- Страница сборника: карточка с токеном (состав и `me` решает collections, D6), режимы game — без токена; 404 collections — 404 экрана, game недоступен или ещё не спроецировал — `modes: unavailable`. `me` — только пользователю.
- Медиатека: аноним — 401, гость — 403 `auth.guest_forbidden` без вызова library; всегда `private, no-store`; 404 library (`library.shelf_not_found`) — 404 экрана.
- Онлайн только с аккаунтом (D13, ADR 0036): `/v1/pages/online` гостю и анониму — `{status: "auth_required", blocks: null}` без вызовов (403 гостя от match по непроверенному токену — тоже); токен не прошёл локальную проверку или match ответил 401 — `session_expired` (фронт делает refresh и повторяет). Блоки `open_rooms`, `my_matches` (match, лимиты `GV_PAGES__ONLINE_*_LIMIT`) и `modes` (game `?session=online`) гаснут независимо. `/v1/pages/match/{id}` — как медиатека (401/403 без вызова match); 404 match — 404 экрана, прочие неописанные 4xx и недоступность — все блоки `unavailable`; карточки catalog и флаги «в медиатеке» (library `/v1/items/{id}/shelves` по единице) — свои блоки под общим сроком `GV_PAGES__MATCH_ITEMS_DEADLINE_S`. Всё онлайн — `private, no-store`.
- Ответ экрана из отказа источника (`screen_error`) — только 401/403/404/409/422; прочие 4xx — блок `unavailable`, а не 404.
