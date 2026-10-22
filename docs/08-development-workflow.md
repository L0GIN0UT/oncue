# Процесс разработки

> Статус: принято (v1). Решение: [ADR 0009](adr/0009-monorepo.md).

## 1. Структура монорепозитория

```
guessverse/
  services/
    identity/ catalog/ media_pipeline/ game/ match/ rating/ collections/ library/
    integrations/ progression/ payments/ social/ notifications/
    bff_web/ admin/                # gateway — это Nginx: infra/nginx
  libs/
    core/          # базовые классы домена, ошибки, id (uuid7), часы, пагинация, контекст запроса
    db/            # SQLAlchemy: движок, таблицы outbox/inbox, Unit of Work, миграции
    messaging/     # конверт событий, outbox relay, inbox, потребители, ретраи, Redpanda/RabbitMQ
    auth/          # проверка JWT по JWKS, Principal, require_perm
    observability/ # логирование (structlog), метрики (Prometheus), трейсинг (OTel)
    service/       # каркас сервиса: ServiceDefinition, процессы, health, Problem Details
    flags/         # фича-флаги (Unleash)
    testkit/       # общий модуль тестирования
    # entitlements/, game_kernel/ — появятся на этапах 2 и 6
  contracts/
    openapi/       # <service>.json — снимок, генерируется `gv contracts export`
    events/        # envelope.v1.json + <service>/<Event>.v<N>.json
  infra/
    services.toml  # реестр сервисов — источник для `gv gen`
    compose/  nginx/  docker/  observability/
    # ansible/, helm/, terraform/ — к релизу (docs/07-infrastructure.md)
  tools/
    gv/            # CLI: gen, new-service, test, lint, dev, contracts, ci
  .github/         # CI (ci.yml), ночной прогон (nightly.yml), шаблон PR, dependabot
  mkdocs.yml       # сайт документации + справочник libs из докстрингов
  # frontend/, e2e/, load/, seeds/ — появятся на этапах 1–2
  docs/
  CLAUDE.md        # выжимка для AI-ассистента; в каждом сервисе — свой services/<name>/CLAUDE.md
  pyproject.toml   # uv workspace
```

## 2. Локальная разработка

Разработка идёт **по одному сервису поэтапно**, но остальная система доступна:

```bash
gv dev secrets > infra/compose/.env   # один раз: .env со случайными паролями (в git не попадает)
gv dev up infra                       # Postgres, Redis, Redpanda, RabbitMQ, S3 (SeaweedFS), OpenSearch, Centrifugo, Unleash
gv dev up services identity catalog   # нужные сервисы в контейнерах (без имён — все + gateway)
gv dev run game                       # разрабатываемый сервис из исходников с hot-reload
gv dev up observability               # Prometheus, Grafana, Tempo, Elasticsearch/Kibana
gv test game --level unit
```

- `gv new-service <name> --description ... [--public PREFIX] [--no-database]` создаёт сервис из шаблона (гексагональная структура, `settings.py`, `main.py`, миграция outbox/inbox, тесты всех уровней, `CLAUDE.md`), добавляет его в `infra/services.toml` и перегенерирует конфиги. Так все сервисы одинаковы с первого коммита.
- `gv gen` перегенерирует из реестра compose, Nginx, Prometheus, `.importlinter`, скрипт БД. Руками эти файлы не правятся; CI проверяет `gv gen --check`.
- `gv contracts export` обновляет снимок OpenAPI и схем событий; `gv contracts check --base origin/main` ищет ломающие изменения.
- `gv dev seed` наполняет поднятый стек для e2e (только local/ci, идемпотентно): каталог и синтетика, исходники медиа и ассеты, наборы дня и недели, кураторские сборники `seeds/collections`, ожидание онлайн-шаблонов 4a и их пулов (подробности — докстринг `tools/gv/src/gv_cli/seed.py`). Учётные данные админа — `GV_BOOTSTRAP_ADMIN__LOGIN|PASSWORD` и `GV_SERVICE_SECRET_ADMIN` из `infra/compose/.env`.
- e2e на полном стеке (`e2e/*.spec.ts`, как job `e2e-smoke`): стек `gv dev up services` + `compose.web.yml`, затем `gv dev seed`, затем `E2E_SEEDED=1 pnpm -C frontend test:e2e:stack` с теми же переменными админа; сценарии, регистрирующие игроков (сборники 3b, онлайн 4a), читают ссылку подтверждения из `docker compose logs identity` (`E2E_COMPOSE_FILES`, `E2E_COMPOSE_DIR`). Адрес стека — `E2E_BASE_URL` (по умолчанию `https://localhost`; он же должен быть в `CENTRIFUGO_ALLOWED_ORIGINS`, иначе онлайн уйдёт на HTTP-tick).

## 3. Порядок разработки нового функционала

1. Если меняется архитектура — ADR.
2. Контракт: OpenAPI и/или JSON Schema события в `contracts/` (contract-first).
3. Домен + unit-тесты.
4. Use-case + порты + unit-тесты на фейках из testkit.
5. Адаптеры + integration-тесты.
6. API + component-тест.
7. Документация в `docs/`, если меняется поведение.

## 4. Git

- Trunk-based: короткоживущие ветки от `main`, PR, squash merge.
- Ветки: `feat/<service>-<кратко>`, `fix/...`, `chore/...`, `docs/...`.
- Коммиты: Conventional Commits со scope-сервисом: `feat(game): add frame_progressive clue`.
- PR: шаблон с чек-листом (тесты, контракты, миграции обратно совместимы, документация, ADR).
- `main` защищён: обязательны зелёный CI и ревью (когда появится команда; до этого — самопроверка по чек-листу).

## 5. Definition of Done

- [ ] Пройдены обязательные проверки из [10-ai-assistant.md](10-ai-assistant.md) для затронутой зоны (особенно §3 — безопасность).

- [ ] Код соответствует [03-code-standards.md](03-code-standards.md), линтеры и import-linter зелёные.
- [ ] Тесты на нужных уровнях ([04-testing.md](04-testing.md)), пороги покрытия соблюдены.
- [ ] Контракты обновлены, совместимость проверена.
- [ ] Миграции обратно совместимы.
- [ ] Метрики, логи и трейсы для нового сценария.
- [ ] Фича за флагом, если она не готова для всех.
- [ ] Документация обновлена.

## 6. Релизы

- SemVer на систему (`vX.Y.Z`), changelog генерируется из коммитов.
- Каждый образ помечен git sha. Релиз = набор sha, зафиксированный тегом.
- Незавершённые функции выкатываются выключенными (фича-флаги), а не держатся в долгих ветках.
