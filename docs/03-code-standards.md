# Стандарты кода

> Статус: принято (v1). Решение: [ADR 0002](adr/0002-hexagonal-architecture.md).

## 1. Структура сервиса

```
services/<name>/
  src/<name>_service/        # суффикс _service исключает конфликты имён (collections и т.п.)
    domain/              # сущности, value objects, доменные сервисы, доменные события, исключения
                         # чистый Python: без FastAPI, SQLAlchemy, Redis, HTTP
    application/
      commands/          # use-cases на изменение: один класс = одна операция
      queries/           # use-cases на чтение (могут использовать read-модели напрямую)
      ports/             # интерфейсы: репозитории, шлюзы к другим сервисам, publisher, clock, uow
      dto.py
    infrastructure/
      db/                # SQLAlchemy-модели, репозитории, миграции (alembic/)
      messaging/         # outbox relay, consumers, обработчики событий
      clients/           # клиенты других сервисов (генерируются из OpenAPI)
      ...
    api/
      public/  internal/  admin/   # FastAPI-роутеры, схемы запросов и ответов
    main.py              # composition root: ServiceDefinition (роутеры, таблицы, обработчики событий)
    settings.py          # pydantic-settings, только env
  tests/
    unit/  integration/  component/  contract/
  CLAUDE.md
  pyproject.toml         # образ — общий infra/docker/python-service.Dockerfile
```

Процессы (`api | relay | consumer | worker | all | migrate`), health, метрики, graceful shutdown
и сборку зависимостей берёт на себя `libs/service` (`gv_service.main(DEFINITION)`): сервис только
объявляет, из чего состоит. Отдельный DI-фреймворк не нужен — зависимости собираются явно
в `main.py` и фабриках роутеров/обработчиков.

**Правило зависимостей** (проверяется `import-linter` в CI):

```
api, infrastructure, main  →  application  →  domain
domain ничего не импортирует из других слоёв
application не импортирует infrastructure и api
сервис не импортирует код другого сервиса (только libs/* и сгенерированные клиенты)
```

## 2. Принципы на практике

| Принцип | Как применяем | Как проверяем |
|---|---|---|
| **SRP** | Один use-case = один класс с методом `__call__`/`execute`. Роутер только мапит HTTP ↔ DTO и вызывает use-case | Ревью; роутер > 15 строк — сигнал |
| **OCP** | Расширение через плагины и реестры (игровой движок, провайдеры, типы уведомлений), а не через `if type == ...` | Ревью: ветвления по типу в ядре запрещены |
| **LSP** | Реализации портов взаимозаменяемы: in-memory и SQL-репозиторий проходят один и тот же набор тестов | Общие contract-тесты портов в testkit |
| **ISP** | Маленькие порты: `CanImportLibrary`, `CanExportPlaylist`, `CanSyncWatchlist` вместо одного `Provider` на всё | Ревью |
| **DIP** | application зависит от `ports`, реализации подключаются в `main.py` (composition root) | import-linter |
| **DRY** | Общая инфраструктура — в `libs/*`. Правила игры — в `libs/game_kernel` | См. ниже |
| **KISS / YAGNI** | Не добавлять абстракции «на будущее» без второго реального случая использования, **кроме** зафиксированных точек расширения (плагины, провайдеры, домены) | Ревью |

### Границы DRY

- **Можно шарить (`libs/`):** логирование, трейсинг, outbox/inbox, клиенты брокеров, auth-зависимости, базовые классы (Entity, AggregateRoot, UnitOfWork), ошибки, пагинацию, testkit, game-kernel.
- **Нельзя шарить:** доменные модели и ORM-модели сервисов. DTO в разных контекстах могут совпадать — это осознанное дублирование ради независимости деплоя.
- `libs/*` версионируются, у каждой есть changelog. Ломающее изменение в lib = major-версия.

## 3. Домен

- Агрегаты защищают инварианты: изменение только через методы агрегата, никаких публичных сеттеров.
- Value objects неизменяемы (`@dataclass(frozen=True)`).
- Доменные события собираются агрегатом (`self._events`). Unit of Work после commit кладёт их в outbox.
- Время и случайность — через порты `Clock` и `Random` (детерминированные тесты, воспроизводимые seed-ы игр).
- Исключения домена — иерархия от `DomainError`, на HTTP мапятся в одном месте.

## 4. API-соглашения

- REST, ресурсы во множественном числе, версия в пути (`/v1`).
- Ошибки — **RFC 9457 (Problem Details)**: `type`, `title`, `status`, `detail`, `code` (машинный, например `game.desync`), `errors[]` для валидации.
- Пагинация курсорная: `?cursor=&limit=`, ответ `{items, next_cursor}`.
- Мутации принимают `Idempotency-Key`. Повтор с тем же ключом возвращает сохранённый ответ.
- Даты в ISO 8601 UTC. Деньги — целые минорные единицы + валюта.
- Каждая ручка описана в OpenAPI. Схема генерируется из кода и коммитится в `contracts/openapi/` (CI сверяет).
- Тексты для пользователя — ключи i18n, а не строки на русском в коде.

## 5. Стиль и инструменты

| Инструмент | Назначение |
|---|---|
| `ruff` (lint + format) | Стиль, импорты, частые ошибки. Строка 100 |
| `mypy --strict` | Типизация. `Any` только с комментарием-обоснованием |
| `import-linter` | Правила слоёв и запрет межсервисных импортов |
| `pre-commit` | ruff, mypy (изменённые), проверка секретов (`gitleaks`), end-of-file |
| `uv` | Менеджер зависимостей и workspace монорепо |
| `bandit`, `pip-audit` | Безопасность |

Фронтенд: ESLint + Prettier, TypeScript `strict`, типы API генерируются из OpenAPI (`openapi-typescript`).

## 6. Именование

- Сервисы и пакеты: `snake_case`, образы `guessverse/<service>`.
- События: `PascalCase` в прошедшем времени (`MatchFinished`). Команды и задачи — повелительное наклонение (`GenerateClueAssets`).
- Use-cases: `StartGame`, `SubmitGuess`, `PublishCollection`.
- Ключи конфигов и фича-флагов: `domain.area.name` (`film.daily.frame`, `billing.enabled`).

## 7. Логи, метрики, трейсы

- Логи — структурированный JSON в stdout (`structlog`): `service`, `env`, `request_id`, `trace_id`, `user_id` (если есть), `event`. Персональные данные в логи не пишем.
- Метрики — Prometheus: RED (rate, errors, duration) на каждую ручку и обработчик, плюс бизнес-метрики (игры стартовали и завершены, матчи, длина очередей матчмейкинга).
- Трейсы — OpenTelemetry autoinstrumentation (FastAPI, SQLAlchemy, Redis, aiokafka, aio-pika) + ручные спаны в use-cases.
