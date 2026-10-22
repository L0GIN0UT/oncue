# 0035. Realtime: токены Centrifugo, namespace-ы, версии и recovery, HTTP-tick

- **Статус:** предложено (план этапа 4)
- **Дата:** 2026-10-22

## Контекст
Centrifugo v6 уже стоит в compose, но ни один сервис его не использует. Нужно решить, кто
выдаёт токены, как клиент узнаёт о пропуске сообщения и что делать без WebSocket.

## Решение
- **Токены — разные секреты (D9):** connection-токен выдаёт identity (`POST
  /v1/realtime/token`, только пользователь, TTL 10 мин, `aud=centrifugo`); subscription-токен
  канала `room:{code}` — match (`POST /v1/rooms/{code}/realtime-token`, только участник).
  Секреты в env (`GV_REALTIME__CONNECTION_TOKEN_SECRET` у identity,
  `GV_REALTIME__SUBSCRIPTION_TOKEN_SECRET` у match; Centrifugo —
  `CENTRIFUGO_CLIENT_TOKEN_HMAC_SECRET_KEY` и `..._SUBSCRIPTION_TOKEN_HMAC_SECRET_KEY`).
  Компрометация match не даёт подделать вход. Поле реестра `realtime` в `infra/services.toml`
  передаёт сервису адрес server API и ключ.
- **Конфиг Centrifugo** — `infra/centrifugo/config.json`: namespace-ы `room` (история 100
  сообщений / 600 с, `force_recovery`) и `personal`; клиенты не публикуют, анонимов нет,
  лимит соединений на пользователя; разрешённые Origin — из env. Gateway: WebSocket
  `/connection/`, фолбэк HTTP-stream `/connection/http_stream` и команды эмуляции
  `/connection/emulation`.
- **Сообщение канала** — `contracts/realtime/room.message.v1.json`: `{version, kind,
  server_now, delta}`; неизвестный `kind` клиент игнорирует (расширение совместимо, удаление —
  новая версия). Пропуск версии → `GET /v1/rooms/{code}/state`. Проверка в тестах —
  `gv_testkit.assert_realtime_message_matches_schema`.
- **Личное в канал не идёт:** свои попытки — только в ответе команды и в `state` по своему
  токену. До `reveal` в канале нет `media_item_id`, названия, ключей ответа и плана.
- **HTTP-tick:** `POST /v1/rooms/{code}/tick` раз в `GV_MATCH__TICK_S` (без WebSocket — чаще)
  — heartbeat присутствия и фолбэк снимка; матч доигрывается при остановленном Centrifugo.

## Последствия
- Без обратных HTTP-вызовов из Centrifugo в match (proxy-события не нужны).
- `user_connection_limit` действует на узел Centrifugo: при нескольких узлах лимит на
  пользователя кратно выше — учесть на этапе 7.
- Фронтенду: `centrifuge` с `emulationEndpoint: '/connection/emulation'`, single-flight
  обновление токенов, явный `wss://` в CSP `connect-src`.
