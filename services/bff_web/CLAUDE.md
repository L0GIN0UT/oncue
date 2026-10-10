# bff_web

Backend-for-frontend: агрегация данных под экраны веб-клиента.

- Пакет: `bff_web_service`; запуск: `bff_web api`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: `/api/web/v1/...` на gateway (только GET)
- Зависимости: Redis (кэш); вызывает game, identity, catalog по HTTP
- Статус: этап 2, шаг 0a — схемы и заглушки 501; реализация — поток C (`docs/plans/stage-2.md` §6)

## Особенности
- Только чтение (D4): мутаций нет, CSRF и идемпотентность не нужны; команды игры браузер шлёт прямо в game.
- Схемы экранов — `api/public/schemas.py`; блок упавшего источника — `{"status": "unavailable"}`, страница всё равно 200.
- Токен — из cookie `gv_access` или Bearer, дальше передаётся как Bearer; недействительный токен в `/v1/session` — `subject: null`, не 500.
- Публичные ответы — `public, max-age=30`, личные — `private, no-store`; личные блоки не кэшируются.
