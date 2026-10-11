# Тестирование и автотесты

> Статус: принято (v1).

## 1. Цели

1. Любой сервис тестируется **изолированно**: одной командой, без запуска остальных сервисов.
2. Совместимость сервисов между собой гарантируется **контрактными тестами**, а не только e2e.
3. Всё автоматически запускается в CI. Merge в `main` без зелёных проверок невозможен.
4. Тестовая инфраструктура пишется **один раз** в общем модуле `libs/testkit` и переиспользуется всеми сервисами.

## 2. Пирамида

| Уровень | Что проверяет | Зависимости | Скорость | Где |
|---|---|---|---|---|
| **Unit** | Домен, use-cases, плагины game_kernel | Никаких IO: фейки портов из testkit | мс | `tests/unit` |
| **Integration** | Адаптеры: репозитории, outbox, consumers, клиенты Redis/брокеров | Реальные Postgres, Redis, Redpanda, RabbitMQ в контейнерах (testcontainers) | секунды | `tests/integration` |
| **Component** | Сервис целиком через его HTTP API и события | Свои реальные зависимости в контейнерах. **Другие сервисы — заглушки** (WireMock-подобные из testkit) | секунды | `tests/component` |
| **Contract** | Совместимость API и событий с потребителями | Схемы из `contracts/` | секунды | `tests/contract` |
| **E2E** | Ключевые пользовательские сценарии через всю систему | Весь стек в docker-compose + фронт (Playwright) | минуты | `e2e/` (корень) |
| **Load** | Производительность и SLO | Staging | минуты | `load/` (k6) |
| **Resilience** | Деградация при падении зависимостей | Staging / compose + toxiproxy | минуты | `e2e/resilience` |

Ориентир по количеству: unit ≫ integration > component > contract ≫ e2e.

## 3. Общий модуль `libs/testkit`

```
libs/testkit/src/testkit/
  containers/        # фикстуры testcontainers: postgres, redis, redpanda, rabbitmq, opensearch; S3 — moto_server процессом
                     # поднимаются один раз на сессию, БД очищается между тестами (транзакция/truncate)
  fakes/             # in-memory реализации портов: InMemoryRepository, FakeClock, FakeRandom(seed),
                     # FakePublisher (собирает события), FakeAssetPort, FakeUnitOfWork
  factories/         # фабрики данных (polyfactory): User, MediaItem, ModeConfig, Collection, Room…
  stubs/             # HTTP-заглушки других сервисов по их OpenAPI (respx/WireMock), сценарии ответов
  events/            # хелперы: publish_and_wait(event), assert_published(EventType, where=…), чтение outbox
  auth/              # выпуск тестовых JWT с нужными ролями и правами
  api/               # асинхронный тестовый клиент сервиса (httpx + ASGI), хелперы Problem Details
  contracts/         # валидация ответов и событий по схемам из contracts/, проверка совместимости
  port_suites/       # общие наборы тестов для портов (LSP): одна suite гоняется против fake и real
  game/              # сценарный DSL для движка: given_mode(...).when_guess(...).then_score(...)
  pytest_plugin.py   # регистрирует фикстуры и маркеры: unit, integration, component, contract, slow
```

**Пример — component-тест сервиса game без остальных сервисов:**

```python
@pytest.mark.component
async def test_daily_frame_full_flow(game_app, stubs, events, factories, auth):
    mode = await factories.published_mode("film.daily.frame")
    stubs.media_pipeline.signed_url.returns_ok()
    token = auth.user(perms=[])

    start = await game_app.post("/v1/games", json={"mode": mode.key}, token=token)
    for _ in range(5):
        await game_app.post(
            f"/v1/games/{start.id}/guess", json={"answer": "Интерстеллар"}, token=token
        )

    events.assert_published("game.GameFinished", where={"mode_key": mode.key})
```

## 4. Как запустить тесты одного модуля

Единая CLI в корне монорепо (`tools/gv`, обёртка над `uv`/`pytest`/`docker compose`):

```bash
gv test game                     # все уровни сервиса game, кроме e2e
gv test game --level unit        # только unit (без Docker)
gv test game --level integration
gv test libs/messaging          # библиотека
gv test --changed                # только затронутые изменениями модули (по git diff и графу зависимостей)
gv test e2e --scenario daily     # e2e-сценарий на поднятом стеке
gv test contracts                # проверка совместимости всех контрактов
```

Каждый сервис также запускается напрямую: `cd services/game && uv run pytest -m unit`.

## 5. Контрактные тесты

- **API:** OpenAPI из кода сравнивается со снимком `contracts/openapi/<service>.json` (`gv contracts check`): снимок должен быть актуален, а удаление операций относительно `main` блокируется. Детальный diff полей (oasdiff) подключается на этапе 1. Ответы component-тестов валидируются по схеме. Дополнительно — property-based фаззинг ручек (`schemathesis`) в nightly.
- **События:** каждое опубликованное в тестах событие валидируется по JSON Schema. Потребитель хранит **примеры событий**, на которые рассчитывает (`tests/contract/consumed/*.json`). CI проверяет, что они валидны по текущей схеме продюсера (лёгкий consumer-driven подход без Pact-брокера).

## 6. Тестирование игрового движка

- Каждый плагин (ClueType, Source, Scorer, Matcher) имеет unit-тесты на граничные значения и **property-based тесты** (Hypothesis): очки монотонно убывают по уровням, `party_speed ≥ 1`, нормализация идемпотентна.
- Golden-тесты matcher-а: таблица «ввод → ожидаемый результат» (опечатки, алиасы, ё/е, partial).
- Детерминизм: с одинаковыми seed и конфигом получается одинаковая последовательность раундов.
- Валидатор конфигов: каждый `ModeConfig` из сидов проходит валидацию в CI.
- Match: тесты конечного автомата с `FakeClock` (таймауты фаз, реконнект, сдача, опережение) и тест передачи lease между двумя инстансами.

## 7. CI (GitHub Actions)

**На каждый PR:**

1. `lint`: ruff, mypy, import-linter, gitleaks — для изменённых модулей.
2. `unit`: матрица по изменённым модулям и зависимым от них.
3. `integration` + `component`: матрица по изменённым сервисам (testcontainers на раннере).
4. `contracts`: совместимость OpenAPI и событий.
5. `build`: Docker-образы изменённых сервисов + Trivy.
6. `coverage gate` (ниже).
7. `e2e-smoke`: короткий набор сценариев на compose (≤ 10 мин) — если затронуты сервисы из сценариев.

**На `main`:** всё выше + полный e2e + публикация образов + деплой на staging.
**Nightly:** полный e2e, schemathesis-фаззинг, resilience-сценарии, тест восстановления из бэкапа.
**Перед релизом:** нагрузочный прогон k6 по ключевым сценариям против SLO ([architecture/scalability-resilience.md](architecture/scalability-resilience.md) §4.3).

### Пороги покрытия

| Слой | Минимум (line/branch) |
|---|---|
| `domain`, `libs/game_kernel` | 90 % |
| `application` | 85 % |
| Сервис в целом | 75 % |
| Падение покрытия в PR | Не более −1 % |

Покрытие — сигнал, а не цель. Обязательное правило: **каждый баг-фикс приходит с тестом, который воспроизводит баг.**

## 8. E2E-сценарии (минимальный набор)

1. Регистрация → онбординг → дейлик (музыка) → результат → награда начислена.
2. Дейлик «Кадр дня» (кино) → сохранить фильм в «смотреть позже».
3. Создание сборника → публикация → игра по сборнику.
4. Дуэль: два клиента, матчмейкинг, матч, итог, изменение Эло.
5. Party на 4 игроков в кастомном лобби, реконнект одного игрока.
6. Жалоба на комментарий → модератор в админке → санкция → уведомление.
7. Удаление аккаунта → данные анонимизированы во всех сервисах.

## 9. Тестовые данные

- Сиды (`seeds/`): справочники, базовые `ModeConfig`, небольшой каталог (≈200 треков, ≈200 фильмов) с готовыми ассетами для локальной разработки и e2e.
- Никаких продовых персональных данных в тестах и на staging.
