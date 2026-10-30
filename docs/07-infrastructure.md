# Инфраструктура и деплой

> Статус: принято (v1). Решение: [ADR 0005](adr/0005-vps-first-kubernetes-ready.md).

## 1. Стратегия

| Этап | Платформа | Когда |
|---|---|---|
| **Сейчас (до релиза и ранний прод)** | Один VPS (или 2: приложение + observability), docker-compose | С первого деплоя |
| **Целевая (после релиза, при росте)** | Kubernetes (managed или k3s-кластер) + Helm | Когда нужен автоскейл, HA нескольких нод или zero-downtime на всех сервисах |

> **КЛЮЧЕВОЕ ТРЕБОВАНИЕ:** всё, что делается для VPS, должно переноситься в Kubernetes **без изменения кода сервисов**. Различаются только манифесты деплоя. Правила ниже (§2) обязательны с первого дня.

## 2. Kubernetes-ready: обязательные правила

1. Один образ на сервис. Процессы (`api`, `consumer`, `worker`, `relay`) — разные команды запуска одного образа.
2. Вся конфигурация — через env (pydantic-settings). Никаких конфиг-файлов, «вшитых» под конкретный хост.
3. Процессы stateless. Локальная ФС только для временных файлов. Всё постоянное — в Postgres, Redis или S3.
4. `/health/live`, `/health/ready` у каждого процесса; `/metrics` — на отдельном внутреннем порту (§8).
5. Корректная обработка SIGTERM (graceful shutdown ≤ 30 с).
6. Логи — JSON в stdout.
7. Миграции БД — отдельный job (`<service> migrate`) перед выкатом, только обратно совместимые.
8. Адреса зависимостей — по DNS-именам сервисов (`postgres`, `redis`, `game-api`), одинаковым в compose и k8s.
9. Ресурсы (`requests/limits`) указаны уже в compose (`deploy.resources`) и переносятся в Helm values.
10. Секреты — только через env или файлы секретов, которые монтирует платформа.

## 3. Деплой на VPS

- **Все 13 доменных сервисов + edge + admin** разворачиваются сразу. Разработка идёт поэтапно ([09-roadmap.md](09-roadmap.md)). Ещё не реализованный сервис разворачивается как заглушка (`/health` + пустые API), чтобы инфраструктура и сеть были финальными с самого начала.
- `infra/compose/`:
  - `compose.infra.yml` — Postgres, PgBouncer, Redis, Redpanda, RabbitMQ, OpenSearch, S3 (SeaweedFS, [ADR 0021](adr/0021-s3-storage-seaweedfs.md)), Centrifugo, Unleash;
  - `compose.services.yml` — сервисы;
  - `compose.observability.yml` — Prometheus, Grafana, Elasticsearch + Kibana + Filebeat, Jaeger/Tempo;
  - `compose.dev.yml` — оверрайды для локальной разработки (hot-reload, открытые порты).
- Обратный прокси — Nginx (gateway): TLS (Let's Encrypt), маршрутизация, rate-limit.
- **Экономия памяти на VPS:** у каждого сервиса есть режим `entrypoint all` — api + consumer + relay в одном процессе (asyncio-задачи). В k8s они разносятся по отдельным deployment-ам. Код один, меняется только команда запуска.

### Оценка ресурсов (стартовая)

| Компонент | RAM |
|---|---|
| 16 сервисов в режиме `all` (~200 МБ) | ~3,5 ГБ |
| Воркеры media-pipeline (ffmpeg) | 1–2 ГБ |
| Postgres + PgBouncer | 2–4 ГБ |
| Redis | 0,5–1 ГБ |
| Redpanda (`--smp 1 --memory 1G`) | 1–1,5 ГБ |
| RabbitMQ | 0,5 ГБ |
| OpenSearch | 1,5–2 ГБ |
| SeaweedFS (S3), Centrifugo, Unleash, Nginx | ~1 ГБ |
| **Итого приложение** | **~12–16 ГБ** |
| Observability (Elasticsearch, Kibana, Prometheus, Grafana, Tempo) | 4–6 ГБ |

**Рекомендация:** VPS приложения 16–32 ГБ / 6–8 vCPU / NVMe. Observability — на отдельном VPS (4–8 ГБ), чтобы падение или переполнение логов не задевало прод. CDN для ассетов — внешний.

## 4. Окружения

| Окружение | Где | Назначение |
|---|---|---|
| `local` | Ноутбук разработчика, compose | Разработка: поднимается инфраструктура + разрабатываемые сервисы, остальные — заглушками или образами из registry |
| `ci` | GitHub Actions | Тесты, testcontainers, e2e на compose |
| `staging` | VPS (меньше прода) | Автодеплой из `main`, e2e, нагрузочные тесты |
| `production` | VPS → k8s | Деплой по тегу релиза с ручным подтверждением |

## 5. CI/CD

```
PR:    lint → unit → integration/component → contracts → build+scan → e2e-smoke   (только изменённые модули)
main:  всё выше → push образов (ghcr.io, тег = git sha) → deploy staging → e2e full
tag:   vX.Y.Z → промоут образов (без пересборки) → deploy production (approval) → smoke → авто-откат при провале
```

- Деплой на VPS: GitHub Actions по SSH → `docker compose pull && up -d` для изменённых сервисов. Перед этим — job миграций.
- Откат: перевыкат предыдущего тега образа (миграции обратно совместимы, см. [architecture/data.md](architecture/data.md) §3).
- Версионирование: SemVer на релиз системы, каждый образ помечен git sha.
- Инфраструктура как код: compose и Nginx в репозитории. VPS настраивается Ansible-плейбуком (`infra/ansible`). Для k8s — Terraform (кластер) + Helm-чарты (`infra/helm`).

## 6. Observability

| Что | Инструмент |
|---|---|
| Логи | Filebeat → Elasticsearch → Kibana (без Logstash ради памяти) |
| Метрики | Prometheus + Grafana, алерты через Alertmanager → Telegram |
| Трейсы | OpenTelemetry Collector → Tempo (или Jaeger) → Grafana |
| Ошибки | Sentry (self-hosted тяжёлый — на старте облачный тариф или GlitchTip) |
| Uptime | Внешний пинг (UptimeRobot или аналог) |

Обязательные дашборды: RED по сервисам, consumer lag, глубина очередей, активные комнаты и игроки, ошибки провайдеров, ресурсы хоста.
Обязательные алерты: 5xx > порога, p95 > SLO, lag > N, DLQ не пуст, диск > 80 %, бэкап не выполнен.

### 6.1. Правила Prometheus и runbook match (realtime и санкции)

Правила лежат в `infra/observability/prometheus/rules/*.yml` (подключены `rule_files` в `prometheus.yml`; каталог монтируется вместе с конфигом). Синтаксис: `docker run --rm --entrypoint promtool -v <каталог prometheus>:/w prom/prometheus check rules /w/rules/match.yml`. Решение — ADR 0063.

| Алерт | Условие | Что делать |
|---|---|---|
| `MatchSanctionsProjectionLag` | лаг последнего применённого `social.UserSanctionsChanged` > 60 с (порог N - константа в выражении) и события шли за 15 мин | consumer match отстаёт от `social.social.v1`: смотреть `gv_events_consumed_total`, DLQ, нагрузку Postgres/Redpanda; пока лаг есть, забаненный игрок может остаться в комнате/очереди. Зависший consumer без новых событий этим правилом не виден - нужен lag группы из экспортёра Kafka |
| `MatchDisconnectFailed` | рост `gv_match_disconnect_failed_total` за 10 мин | `POST /api/disconnect` Centrifugo не удался после `GV_MATCH__UNSUBSCRIBE_ATTEMPTS` попыток: проверить доступность Centrifugo и `GV_REALTIME__API_KEY` у match; забаненный аккаунт держит WebSocket до TTL токена (ADR 0059) - при необходимости отозвать вручную через server API |
| `MatchUnsubscribeFailed` | рост `gv_match_unsubscribe_failed_total` за 10 мин | то же для `unsubscribe` (кик, выход, санкция `ONLINE`, ADR 0052) |
| `MatchRevocationDropped` | рост `gv_match_revocation_dropped_total` за 10 мин | очередь отзыва (`GV_MATCH__REVOCATION_QUEUE_SIZE`) переполнена или сброшена при остановке дольше `GV_MATCH__REVOCATION_DRAIN_S`: Centrifugo медленный/недоступен; после восстановления повторить отзыв вручную для затронутых игроков |

## 7. Переход на Kubernetes (план)

1. Helm-чарт `service` (общий шаблон) + values на каждый сервис. Сгенерировать из compose-описания.
2. Сначала stateless-сервисы в кластер, stateful-хранилища — на managed-сервисы (Postgres, Redis, S3) или операторы (CloudNativePG, Redis operator, Redpanda operator).
3. HPA для API, KEDA для воркеров и consumer-ов, PodDisruptionBudget, anti-affinity для реплик.
4. Ingress (Nginx/Envoy) вместо Nginx на VPS, cert-manager.
5. NetworkPolicy: internal и admin API закрыты снаружи.
6. Переключение трафика через DNS с параллельной работой VPS как fallback.

## 8. Переменные, сертификаты и порты (этап 1)

**`.env` для compose.** Файл `infra/compose/.env.example` генерирует `gv gen` (шаблон `env.example.j2`), руками его не правят. `gv dev secrets > infra/compose/.env` заполняет пустые секреты случайными значениями.

| Переменная | Кто читает | Примечание |
|---|---|---|
| `GV_MEDIA_SIGNING_SECRET` | gateway (`secure_link`) | Без неё gateway не стартует |
| `GV_MEDIA__SIGNING_SECRET` | media_pipeline | Должна совпадать с `GV_MEDIA_SIGNING_SECRET`: `gv dev secrets` подставляет в обе одно значение ([ADR 0012](adr/0012-signed-asset-links-nginx.md)) |
| `GV_MEDIA_ASSETS_BUCKET` | gateway (`/media/` → S3) | По умолчанию `gv-assets`; должен совпадать с `GV_S3__BUCKET_ASSETS` media_pipeline |
| `GV_ADMIN__SECRET_KEY` | admin | Ключ Django. Обязателен в production, локально — случайный на процесс |
| `GV_ADMIN__IP_ALLOWLIST` | admin | CIDR, откуда доступна админка. Вне local/ci обязателен и не пуст — иначе процесс не стартует |
| `GV_PUBLIC_ORIGINS` | identity, game | JSON-массив Origin веб-клиента (dev — `["https://localhost"]`): только с них принимаются мутации по cookie (CSRF) |
| `GV_JWT__SIGNING_KEYS` | identity | JSON `[{kid, private_pem, active}]`; `gv dev secrets` генерирует Ed25519-ключ. Пусто — временный ключ (только local/ci) |
| `GV_GAME__DAILY_SEED_SECRET` | game | Секрет розыгрыша наборов дня; без него compose не стартует |
| `GV_SERVICE_SECRET_<CLIENT>`, `GV_SERVICE_CLIENTS` | game, admin, match, progression, notifications, bff_web / identity | Сервисные токены (D13 плана этапа 2): в `GV_SERVICE_CLIENTS` — sha256 секретов |
| `CENTRIFUGO_API_KEY` | Centrifugo, match | Ключ server API; match получает его как `GV_REALTIME__API_KEY` (identity — нет) |
| `CENTRIFUGO_TOKEN_HMAC_SECRET` | Centrifugo, identity | Секрет connection-токенов (`GV_REALTIME__CONNECTION_TOKEN_SECRET` у identity) |
| `CENTRIFUGO_SUBSCRIPTION_HMAC_SECRET` | Centrifugo, match | Секрет subscription-токенов каналов комнат (`GV_REALTIME__SUBSCRIPTION_TOKEN_SECRET` у match); **обязан отличаться** от секрета connection-токенов, секреты ≥ 32 символов |
| `CENTRIFUGO_ALLOWED_ORIGINS` | Centrifugo | Origin веб-клиента (dev — `https://localhost`); без него compose не стартует |
| `GV_SERVICE_SECRET_BFF_WEB` | bff_web | Секрет сервисного токена bff_web для `game /internal/v1/daily/results` (область `daily.results`) и `progression /internal/v1/levels` (область `levels.read`); его sha256 — в `GV_SERVICE_CLIENTS` identity (запись `bff_web` генерирует `gv gen`) |
| `GV_SERVICE_SECRET_MATCH` | match | Секрет сервисного токена match для game и media-pipeline; его sha256 — в `GV_SERVICE_CLIENTS` identity |

**Старый `.env` и новые клиенты сервисных токенов.** `gv dev secrets` создаёт файл целиком и существующий `.env` не дополняет. Если стенд поднят до появления клиента (`bff_web`, `progression`, `notifications`), compose не стартует: нет `GV_SERVICE_SECRET_<CLIENT>` (`${...:?задайте ...}`), а без записи клиента в `GV_SERVICE_CLIENTS` identity отвечает на выдачу токена отказом, и блоки bff «очки друзей в дейлике» и «уровни игроков» приходят `unavailable`. Что сделать: сгенерировать секрет (`gv dev secrets` в временный файл, взять из него только нужные строки `GV_SERVICE_SECRET_<CLIENT>`), дописать их в `.env`, добавить в JSON `GV_SERVICE_CLIENTS` запись `{"client_id":"bff_web","secret_sha256":"<sha256 секрета>","audiences":{"game":["daily.results"],"progression":["levels.read"]}}` (актуальный состав записей — `SERVICE_CLIENTS` в `tools/gv/src/gv_cli/generate.py` и `.env.example`), затем пересоздать identity и клиента. Секреты в логи и тикеты не копировать.

**Онлайн (этап 4a).**

- **Процесс `rooms`.** Владелец комнат match ([ADR 0034](adr/0034-rooms-process-lease-snapshots.md)): в compose входит в команду `all` образа match, в Kubernetes — отдельный Deployment (`match rooms`). При SIGTERM: остановка (`GV_RUNNERS__STOP_TIMEOUT_S`, 5 с) + передача комнат (`GV_RUNNERS__HANDOFF_TIMEOUT_S`, 10 с) должны быть меньше `shutdown_timeout_s` (25 с) и `stop_grace_period` (30 с) — иначе валидация настроек не даст стартовать. Падение runner-а завершает процесс с ненулевым кодом.
- **`GV_REALTIME__*`** (сервисы с `realtime = true` в `infra/services.toml`, [ADR 0035](adr/0035-realtime-centrifugo-tokens-and-tick.md)): `API_URL` + `API_KEY` задаются только вместе (match), `CONNECTION_TOKEN_SECRET` — identity, `SUBSCRIPTION_TOKEN_SECRET` — match; `TOKEN_AUDIENCE` (`centrifugo`, как в конфиге Centrifugo), `TOKEN_TTL_S` (≤ 600), `TIMEOUT_S`. Секреты короче 32 символов и совпадение двух секретов — ошибка старта.
- **`GV_MATCH__*`** — лимиты и параметры `rooms`, полный перечень с умолчаниями и описанием — `services/match/src/match_service/settings.py`. Ключевые: `LEASE_TTL_S` (10) и `LEASE_RENEW_S` (3, не больше половины TTL), `REPLY_TIMEOUT_MS` (1500), `TICK_S` (5) и `PRESENCE_TIMEOUT_S` (12), `MAX_ROOMS_PER_INSTANCE` (300), `ROOMS_POLL_MS` (250, меньше `socket_timeout` Redis 2 с), `CLOSED_TTL_S`, `SNAPSHOT_TTL_H`, `GAME_URL`/`MEDIA_URL` и таймауты, `BREAKER_*`. Игровые величины (таймеры, очки, число раундов) здесь не живут — только в конфиге режима.
- **Centrifugo.** Образ `centrifugo/centrifugo:v6.9.7` (точная версия; обновляет Dependabot), движок Redis (db 1), конфиг `infra/centrifugo/config.json` монтируется read-only: namespace-ы `room` (история 100 / 600 с, recovery) и `personal`, анонимов и публикации клиентов нет, `user_connection_limit` 10. Секреты и Origin — только из env (`CENTRIFUGO_*` перекрывают файл). Gateway: `/connection/` (WS), `/connection/http_stream`, `/connection/emulation`.
- **Redis match** — AOF включён: потеря Redis теряет активные комнаты (история матчей — в Postgres).

Dev-оверлей (`compose.services.dev.yml`) монтирует `seeds/game/modes` в game (`GV_GAME__BOOTSTRAP_MODES_DIR`) и `seeds/rating/ladders` в rating (`GV_RATING__BOOTSTRAP_LADDERS_DIR`), `seeds/progression` в progression (`GV_PROGRESSION__BOOTSTRAP_CONFIG_DIR`, версия 1 конфига) и включает у identity `GV_EMAIL__BACKEND=log` (код из письма пишется в лог, только local/ci; [ADR 0041](adr/0041-email-codes-trusted-devices.md)).

**Засев стенда.** `gv dev seed` наполняет стенд синтетикой, `gv dev seed-real` дополняет его реальными открытыми медиа из `C:/gv-media`. Чистый стенд только на реальных данных — `gv dev seed-real --without-synthetic`: синтетика пропускается, выполняются базовые шаги (онлайн-шаблоны, предметы, события, social), дейлики и «рекомендуемое» строятся из сборников `real-*`, а режим без материала попадает в итоговую сводку, не прерывая засев. Подробности — `seeds/media/README.md`.

Остальные настройки сервисов описаны в `settings.py` сервиса и в [плане этапа 1](plans/stage-1.md).

**TLS на gateway.** Сертификаты лежат в `infra/nginx/certs/{fullchain,privkey}.pem`, каталог в `.gitignore`. Команды выпуска — в шапке `infra/compose/compose.gateway.yml`:

- dev — самоподписанный сертификат (`openssl`);
- prod — первичный выпуск через certbot (webroot), продление — сервис `certbot` (профиль `certbot`), затем `nginx -s reload`.

Порт 80 только редиректит на 443 и отвечает на ACME-challenge.

**Метрики.** Каждый процесс (api, consumer, worker, relay) отдаёт `/metrics` на отдельном порту `GV_METRICS_PORT` (по умолчанию 9100). На основном порту `/metrics` нет, gateway его не публикует, Prometheus собирает `:9100`. В `gv dev run <service>` порт метрик равен `dev_port + 1000`, чтобы локальные сервисы не конфликтовали.
