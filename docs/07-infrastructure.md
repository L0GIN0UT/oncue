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
  - `compose.infra.yml` — Postgres, PgBouncer, Redis, Redpanda, RabbitMQ, OpenSearch, MinIO, Centrifugo, Unleash;
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
| MinIO, Centrifugo, Unleash, Nginx | ~1 ГБ |
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
| `GV_MEDIA_ASSETS_BUCKET` | gateway (`/media/` → MinIO) | По умолчанию `gv-assets`; должен совпадать с `GV_S3__BUCKET_ASSETS` media_pipeline |
| `GV_ADMIN__SECRET_KEY` | admin | Ключ Django. Обязателен в production, локально — случайный на процесс |
| `GV_ADMIN__IP_ALLOWLIST` | admin | CIDR, откуда доступна админка. Вне local/ci обязателен и не пуст — иначе процесс не стартует |

Остальные настройки сервисов описаны в `settings.py` сервиса и в [плане этапа 1](plans/stage-1.md).

**TLS на gateway.** Сертификаты лежат в `infra/nginx/certs/{fullchain,privkey}.pem`, каталог в `.gitignore`. Команды выпуска — в шапке `infra/compose/compose.gateway.yml`:

- dev — самоподписанный сертификат (`openssl`);
- prod — первичный выпуск через certbot (webroot), продление — сервис `certbot` (профиль `certbot`), затем `nginx -s reload`.

Порт 80 только редиректит на 443 и отвечает на ACME-challenge.

**Метрики.** Каждый процесс (api, consumer, worker, relay) отдаёт `/metrics` на отдельном порту `GV_METRICS_PORT` (по умолчанию 9100). На основном порту `/metrics` нет, gateway его не публикует, Prometheus собирает `:9100`. В `gv dev run <service>` порт метрик равен `dev_port + 1000`, чтобы локальные сервисы не конфликтовали.
