# match

Онлайн: лобби, комнаты, матчмейкинг, конечный автомат матча, таймеры, реконнект, наблюдение.

- Пакет: `match_service`; запуск: `match api|relay|consumer|rooms|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: `/api/match/v1/...` на gateway; admin — `/admin/v1/rooms|matches`
- Зависимости: PostgreSQL, Redis, Redpanda, Centrifugo; game (`round-plans`), media-pipeline (`asset-links`)
- Статус: этап 4a — комнаты, кастомные лобби, дуэль, party, realtime, реконнект (`docs/plans/stage-4.md` §5, ADR 0032–0036)

## Особенности
- Комнаты живут в Redis, владелец — процесс `rooms` (ADR 0034): `api` кладёт команду в `{room:CODE}:cmd` и ждёт ответ в `{room:CODE}:reply:{user}:{op}` ≤ `GV_MATCH__REPLY_TIMEOUT_MS`, иначе 202. Права (участник, хост) проверяет владелец — `application/owner.py`.
- Lease `{room:CODE}:owner = instance|epoch`; снимок пишется только Lua-CAS `Lease.write` с токеном эпохи. Курсор потока хранится в снимке — это и есть подтверждение команд (`XDEL` и ответы — после снимка): команды без снимка новый владелец применит заново.
- Снимок — `infrastructure/rooms/codec.py` со `schema_version`; новое поле — только со значением по умолчанию при чтении, смена смысла — `SCHEMA_VERSION + 1` и шаг `_UPGRADES`; тест читает сохранённый `tests/unit/data/room_snapshot.v1.json`.
- Правила раунда — только фасад ядра `ModeEngine.contest_*` (`application/rules.py`), без `if kind == ...`. Единственное правило по составу — пауза при отключении в матче на двоих (D10). Число раундов не пишется в тело конфига: его задаёт план game.
- Версия комнаты растёт на каждое сообщение канала (без пропусков — фронт ресинкается по пропуску); `phase_version` — версия начала текущей фазы/уровня: `expected_version` старше неё — 409 `match.phase_mismatch`.
- Античит: виды строятся в `application/views.py` из снимка без ядра; план и ключи ответа — только в снимке; `reveal` — только в фазе `reveal`; личное (`me`) — только в ответе команды и `GET /state`. Сценарии обходят JSON рекурсивно (`tests/component/test_scenarios.py`).
- Ссылка уровня — одна на (комната, раунд, уровень): субъект `room:{code}:{round}`, ревил — `…:reveal`; недоступный ассет — раунд `void`.
- Итог — `matches` + `match_players` + `match_rounds` + outbox одной транзакцией, `ON CONFLICT` по `match_id`; флаги `started_written`/`finished_written` в снимке — повтор после handoff безопасен. `match_rounds` партиционирована по месяцу (задача `match.partitions.ensure` в relay).
- Итог пишет runner **после** успешного CAS снимка (`RoomOwner.persist`); пока итог не записан, закрытая комната не отпускается (повтор с паузой, `gv_match_persist_failures_total`). Матч, прерванный до конца отсчёта (`MatchStarted` не было), в Postgres не пишется и `MatchFinished` не публикует. Удалённые аккаунты (`users_proj.deleted` и `Room.forgotten`) в итоге — `00000000-…` без `user_id`.
- Seed плана — `secrets.token_hex(32)` через порт `seeds`, только в снимке. Окно `op_id` — компактное (статус, код, вердикт), только для занятых мест (и добровольного выхода — повтор `leave` даёт тот же ответ), не больше `GV_MATCH__OP_USERS_MAX` игроков; чистится при кике и удалении аккаунта.
- Сбой обработчика не `AppError` откатывает комнату (копия до команды) и пробрасывается: runner перечитывает снимок и повторяет запись. Redis/сеть — повтор без счёта; та же запись упала `GV_MATCH__POISON_LIMIT` раз — 503 `match.unavailable`, команда пропущена. Столько же сбойных тиков подряд — комната прерывается (`internal_error`).
- `player:{id}:room = code|ms` с TTL снимка: занят, если комната по указателю не закрыта и игрок не вышел (или места ещё нет), либо комнаты нет, а указатель свежее `GV_MATCH__PENDING_ROOM_MS`; замена — Lua-CAS (`claim_room`). Итог и `UserDeleted` сериализуются advisory-замком пользователя.
- Свободный чат — только в `lobby`/`reveal`/`summary`; у эмоций свой лимит и `CHAT_SLOW`. Кик/выход/удаление отзывают подписку Centrifugo (`unsubscribe`, сбой — только лог). Админский abort пишется в `admin_actions` (без БД — 503; ключ на другую комнату — 409 `idempotency.key_reused`); в событии — код `admin`. Параллельные `POST /rooms` одного игрока — замок `player:{id}:creating` с токеном (`GV_MATCH__CREATE_LOCK_MS`), до лимита; тот же ключ во время создания — 409 `idempotency.in_progress`.
- Текущая версия шаблона для `POST /rooms` — проекция `mode_heads` из `game.mode_config.v1`, тело — `GET /internal/v1/modes/{key}/versions/{v}` game (кэш без срока).
- Сдача (`forfeit`) — исход матча, не выход: место остаётся, игрок не активен в матче, голосует за реванш. LEFT — только `leave`, выбывание по grace, кик, удаление аккаунта.
- Реванш дуэли/party — новая комната, старая закрывается с `closed {reason: rematch, rematch_code}`; кастомное лобби возвращается в `lobby` с памятью сыгранного. `state`/`tick` читают закрытую комнату, пока жив снимок (`GV_MATCH__CLOSED_TTL_S`).
- Тесты: двойники портов match и сборка сервиса — `tests/support/` (`match_doubles`, `match_harness`, `match_app`, `match_fixtures`); фейки game/media/Centrifugo — `gv_testkit.match`; шаблоны — сиды `seeds/game/modes`.
