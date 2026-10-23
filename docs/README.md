# Документация Guessverse

Единая точка входа. Каждый файл отвечает за одну тему. Если тема уже описана в другом файле, здесь ставится ссылка, а не копия.

## Карта документов

| Файл | О чём | Кому читать |
|---|---|---|
| [01-concept.md](01-concept.md) | Продукт: режимы, загадки, онлайн, экосистема | Всем |
| [architecture/README.md](architecture/README.md) | Обзор архитектуры, принципы, схема | Всем разработчикам |
| [architecture/services.md](architecture/services.md) | Карточки сервисов: ответственность, данные, API, события | Бэкенд |
| [architecture/communication.md](architecture/communication.md) | Sync/async, брокеры, outbox, саги, контракты | Бэкенд |
| [architecture/data.md](architecture/data.md) | Хранилища, кэши, партиционирование, бэкапы | Бэкенд, DevOps |
| [architecture/scalability-resilience.md](architecture/scalability-resilience.md) | Масштабирование, отказоустойчивость, деградация, производительность | Бэкенд, DevOps |
| [architecture/security.md](architecture/security.md) | Аутентификация, роли, секреты, античит | Бэкенд |
| [02-game-engine.md](02-game-engine.md) | Плагинная модель режимов и загадок, конфиги | Бэкенд (game, match) |
| [03-code-standards.md](03-code-standards.md) | Структура сервиса, SOLID/DRY на практике, стиль, API-соглашения | Всем разработчикам |
| [04-testing.md](04-testing.md) | Общий модуль тестирования (testkit), уровни тестов, автотесты в CI | Всем разработчикам |
| [05-admin-panel.md](05-admin-panel.md) | Админ-панель на Django, роли и права, модерация | Бэкенд |
| [06-payments.md](06-payments.md) | Подписки, биллинг (выключен на старте), entitlements | Бэкенд |
| [07-infrastructure.md](07-infrastructure.md) | VPS → Kubernetes, окружения, CI/CD, observability | DevOps |
| [08-development-workflow.md](08-development-workflow.md) | Монорепо, локальная разработка, git, релизы | Всем разработчикам |
| [09-roadmap.md](09-roadmap.md) | Этапы разработки | Всем |
| [backlog.md](backlog.md) | Небольшие доработки вне этапа | Всем |
| plans/: [этап 1](plans/stage-1.md), [этап 2](plans/stage-2.md) | Детальные планы этапов (потоки, контракты, критерии готовности) | Всем разработчикам |
| [10-ai-assistant.md](10-ai-assistant.md) | Скиллы и агенты: что и когда использовать, обязательные проверки безопасности, правила комментариев | Claude, все разработчики |
| [handoff/](handoff/) | Передача задачи между сессиями Claude Code: состояние, решения владельца, открытые вопросы, план | Claude |
| [adr/](adr/README.md) | Записи архитектурных решений (почему так, а не иначе) | Всем |
| [reference/songless-reference.md](reference/songless-reference.md) | Разбор Songless: референс механик | Продукт, бэкенд |

## Правила ведения документации

1. Документация лежит в репозитории рядом с кодом и меняется в тех же PR, что и код.
2. Архитектурно значимое решение оформляется как ADR (`adr/NNNN-название.md`). Принятый ADR не редактируется: при смене решения пишется новый ADR со ссылкой «заменяет NNNN».
3. Контракты API (OpenAPI) и событий (JSON Schema) не дублируются в markdown. Они лежат в `contracts/`, а документы на них ссылаются.
4. В каждом документе в начале указан статус: `черновик` / `принято` / `устарело`.
