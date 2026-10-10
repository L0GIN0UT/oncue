# game

Соло-движок: сессии, раунды, ответы, очки; реестр плагинов загадок; конфиги режимов; дейлик, больше/меньше, топ-32.

- Пакет: `game_service`; запуск: `game api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: `/api/game/v1/...` на gateway; admin — `/admin/v1/mode-configs|daily-sets|projection`
- Зависимости: PostgreSQL, Redis, Redpanda; `gv_game_kernel` (правила), `gv_text` (нормализация)
- Статус: этап 2, шаг 0a — схемы API и заглушки 501 (`common.not_implemented`); реализация — поток B (`docs/plans/stage-2.md` §5)

## Особенности
- Контракт API зафиксирован в 0a (§2.6 плана): схемы — `api/public/schemas.py`, `api/admin/schemas.py`; правка схемы = правка `contracts/openapi/game.json` (`gv contracts export`).
- Правила игры — только в `libs/game_kernel` (плагины, `ModeEngine`); game оркестрирует, без `if type == ...`.
- Конфиг режима: контракт `contracts/modes/mode_config.v1.json`, модели — `gv_game_kernel.spec` (меняет только шаг 0); сиды — `seeds/game/modes/*.yaml`.
- Ассеты ищутся по `(media_item_id, clue_type, params_hash(clue.params))` из `AssetsReady` (D7); очки уровней — в `scoring.params.points`.
- Античит (D11): до ревила в ответах нет `media_item_id`, названия, `asset_set_id`; ассет — только уровня ≤ текущего; чужая сессия — 404.
- Параметр пути admin-ручек — `{mode_key}`: имя `key` конфликтует с параметром зависимости `Idempotency-Key`.
- Периодические задачи (наборы дня, брошенные сессии) — `ServiceDefinition.build_periodic_jobs`, выполняются в relay под advisory lock; должны быть идемпотентными.
