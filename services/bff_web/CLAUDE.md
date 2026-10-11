# bff_web

Backend-for-frontend: агрегация данных под экраны веб-клиента.

- Пакет: `bff_web_service`; запуск: `bff_web api`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: `/api/web/v1/...` на gateway (только GET)
- Зависимости: Redis (кэш); вызывает game, identity, catalog по HTTP
- Статус: этап 2 — экраны session, home, solo, results (`docs/plans/stage-2.md` §6)
- Граф зависимостей собирает `wiring.py` (вне слоёв); `api/deps.py` — что получают роутеры.

## Особенности
- Только чтение (D4): мутаций нет, CSRF и идемпотентность не нужны; команды игры браузер шлёт прямо в game.
- Схемы экранов — `api/public/schemas.py`; блок упавшего источника — `{"status": "unavailable"}`, страница всё равно 200.
- Токен — из cookie `gv_access` или Bearer, дальше передаётся как Bearer; недействительный токен в `/v1/session` — `subject: null`, не 500.
- Публичные ответы — `public, max-age=30`, личные — `private, no-store`; личные блоки не кэшируются.
- Токен проверяется локально (JWKS identity, `GV_JWKS_URL`): недействительный → аноним (блок дейликов `unavailable`, game не вызывается); JWKS недоступен → токен уходит источникам непроверенным. Вид субъекта — по claim `gst`, имя — из identity `/v1/me` (гостю он отвечает 403).
- Источники: `GV_UPSTREAMS__{GAME,CATALOG,IDENTITY}_URL`, общий таймаут `timeout_s` (1,5 с), лимит параллельных вызовов на запрос, circuit breaker на сервис (`breaker_failures`/`breaker_reset_s`); 5xx/429/таймаут/ответ вне контракта → блок `unavailable`, 4xx → решает use-case. Редиректам не следуем (Bearer не уходит по чужому адресу).
- Кэш Redis (`GV_PAGE_CACHE__*`): только публичные `game /v1/modes` (30 с) и карточки catalog (5 мин); в кэш попадает тело, прошедшее разбор; ошибки Redis — промах. Всем страницам — `Vary: Authorization, Cookie`.
