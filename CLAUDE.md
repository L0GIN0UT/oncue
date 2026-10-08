# Guessverse — контекст для Claude

Квиз-экосистема: музыка + кино (фильмы, сериалы, аниме). Соло-режимы, онлайн (дуэль 1×1, party до 6), сборники пользователей, медиатека, интеграции с внешними сервисами. Референс механик — Songless.

## Читать по ситуации (не всё подряд)
- Карта документов: `docs/README.md`
- Какой скилл когда использовать: `docs/10-ai-assistant.md`
- Сервисы и их зоны: `docs/architecture/services.md`
- Игровой движок: `docs/02-game-engine.md`
- Код и тесты: `docs/03-code-standards.md`, `docs/04-testing.md`
- Работая в сервисе, сначала прочитать `services/<name>/CLAUDE.md`

## Нерушимые правила
1. 13 доменных сервисов + gateway, bff-web, admin. У каждого своя БД. **Чужую БД не читать и не писать** (исключение: admin читает реплики).
2. Внутри сервиса: `domain → application → infrastructure/api`. Домен без фреймворков. Межсервисные импорты запрещены (import-linter).
3. Межсервисное общение — события (Redpanda) через **transactional outbox**, потребители идемпотентны (inbox). Задачи — RabbitMQ. Синхронные цепочки A→B→C запрещены.
4. **Ничего не хардкодить:** режимы, уровни, очки, лимиты, цены — конфиги. Новые загадки и режимы — плагины game-kernel, без `if type == ...` в ядре.
5. Все мутации идемпотентны (`Idempotency-Key` / `opId`). Деньги — целые копейки.
6. Контракты (OpenAPI, JSON Schema событий) в `contracts/` — меняются первыми, ломающие изменения только с новой версией.
7. Каждая правка с тестом. Баг-фикс — с тестом, воспроизводящим баг. Тестовая инфраструктура — только из `libs/testkit`.
8. Код должен работать и на VPS (compose), и в Kubernetes: env-конфиг, stateless, health-пробы, SIGTERM.
9. payments, identity, ledger — повышенный уровень проверок безопасности (`docs/10-ai-assistant.md` §3).
10. Биллинг выключен (`billing.enabled=false`, план `free_all`). Не включать без явного решения владельца.

## Стек
Python 3.13, FastAPI, SQLAlchemy 2 async, Pydantic v2, Alembic, uv · PostgreSQL, Redis, OpenSearch, Redpanda, RabbitMQ, Centrifugo, MinIO/S3, Unleash · Django (admin) · Next.js + TS · Docker, GitHub Actions.

## Команды
`gv dev up infra` · `gv dev run <service>` · `gv test <service> [--level unit|integration|component|contract]` · `gv test --changed` · `gv lint` · `gv gen` · `gv contracts export|check` · `gv new-service <name> --description ...`

Состав сервисов — `infra/services.toml`; производные конфиги (compose, nginx, prometheus, .importlinter) только через `gv gen`, руками не править.

## Язык
Документация, докстринги и комментарии — русский. Идентификаторы — английский. Коммиты — Conventional Commits.
