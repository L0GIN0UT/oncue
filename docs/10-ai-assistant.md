# Работа с AI-ассистентом: скиллы и зоны ответственности

> Статус: принято (v1). Документ для Claude и для разработчика: какой инструмент в какой момент использовать.

## 0. Проектный набор `.claude/` (адаптация everything-claude-code)

Набор [everything-claude-code](https://github.com/WorldFlowAI/everything-claude-code) (MIT) перенесён в репозиторий **не целиком, а адаптированным**: оригинал рассчитан на JS/TS (Prettier, tsc, npm), а условия его хуков записаны выражениями вида `tool == "Bash" && ...`, тогда как `matcher` в Claude Code — шаблон имени инструмента (такие хуки не срабатывают). Состав и отличия — в `.claude/README.md`.

| Что | Где | Как работает |
|---|---|---|
| Агенты (8) | `.claude/agents/` | planner, architect, tdd-guide, code-reviewer, security-reviewer, build-error-resolver, refactor-cleaner, doc-updater — каждый знает суть проекта и свои правила |
| Команды (10) | `.claude/commands/` | `/plan`, `/tdd`, `/review`, `/verify`, `/build-fix`, `/refactor-clean`, `/update-docs`, `/checkpoint`, `/learn`, `/orchestrate` |
| Правила | `.claude/rules/` | Подгружаются **только для затронутых путей** (`paths:`): python, tests, migrations, contracts, infra, docs, зона безопасности; `workflow.md` — всегда |
| Хуки | `.claude/settings.json` → `gv hook <имя>` | Логика на Python в `tools/gv/src/gv_cli/hooks.py`, покрыта тестами |
| Скиллы | `.claude/skills/` | `guessverse-conventions`, `guessverse-new-service`, `strategic-compact`, `learned-*` (через `/learn`) |
| Память сессий | `.claude/sessions/` (не в git) | SessionEnd/PreCompact пишут заметку дня; `/checkpoint` заполняет её; SessionStart показывает «Для следующей сессии» |

**Хуки:**

| Событие | Хук | Действие |
|---|---|---|
| SessionStart | `session-start` | В контекст: ветка, незакоммиченные файлы, текущий этап roadmap, заметки прошлой сессии |
| PreToolUse Edit/Write | `guard-edit` | Блокирует правку сгенерированных файлов, снимка контрактов, `.env`, `uv.lock` — с подсказкой, что делать |
| PreToolUse Edit/Write | `compact` | После 50 правок и далее каждые 25 — подсказка про `/checkpoint` + `/compact` |
| PreToolUse Bash | `guard-bash` | Блокирует force-push, `--no-verify`, `compose down -v`, `reset --hard`, `clean -f` |
| PostToolUse Edit/Write | `format` | `ruff check --fix` + `ruff format` по изменённому `.py`; неисправимое — возвращается Claude на доработку |
| Stop | `stop` | Предупреждает об `print`/`breakpoint` в изменённых исходниках |
| PreCompact, SessionEnd | `pre-compact`, `session-end` | Создают/обновляют заметку дня в `.claude/sessions/` |

Права (`permissions`) в `.claude/settings.json` разрешают проверки и тесты без подтверждения и запрещают чтение `.env`, ключей и сертификатов. Личные настройки — в `.claude/settings.local.json` (не в git).

## 1. Порядок загрузки контекста (экономия токенов)

1. **Всегда первым** читается `CLAUDE.md` в корне: выжимка проекта, правила, карта документов.
2. Затем `services/<name>/CLAUDE.md` того сервиса, с которым идёт работа.
3. Из `docs/` — **только документ по теме задачи** (карта в [README.md](README.md)). Всю документацию подряд не читать.
4. Документацию библиотек брать через **context7** (точная версия), а не через веб-поиск и не по памяти.
5. Широкий поиск по коду («где используется», «как устроено X») — через агента `feature-dev:code-explorer`. Он возвращает вывод, а не дамп файлов.
6. Скиллы подключаются по таблице ниже, а не «на всякий случай».

## 2. Карта: этап работы → инструмент

| Этап / ситуация | Обязательно | По необходимости |
|---|---|---|
| **Новая фича** (больше одного файла) | `/plan` (агент `planner`) → `/tdd` (агент `tdd-guide`) | `/orchestrate feature`; `feature-dev:code-explorer` для разбора чужого кода |
| **Новый бэкенд-компонент** (endpoint, consumer, worker, cron) | `backend-design:think-before-coding` → `backend-design:design` | `backend-design:component-architect` |
| **Архитектурное решение / новая технология** | агент `architect` + `backend-design:boring-by-default` | агент `backend-design:boring-tech-advisor`; ADR по итогам |
| **Схема БД, ORM-модели** | `backend-design:data-modeling-discipline` | агент `backend-design:schema-reviewer` |
| **Миграция** | `backend-design:migration-safety` + `backend-design:review-migration` | — |
| **Запросы, репозитории** | `backend-design:query-discipline` | `backend-design:hunt-n-plus-one`, `backend-design:explain-this-query` |
| **Изменение состояния, события, вызовы внешних API** | `backend-design:idempotency-and-side-effects` (outbox, `opId`) | — |
| **Обработка ошибок, ретраи, деградация** | `backend-design:error-handling-as-design` | агенты `backend-design:incident-thinker`, `pr-review-toolkit:silent-failure-hunter` |
| **Отказоустойчивость перед выкатом компонента** | агент `backend-design:incident-thinker` | — |
| **Логи, метрики, трейсы** | `backend-design:observability-by-default` | `observability:setup-otel-propagation` |
| **Производительность, масштаб** | `backend-design:performance-and-scaling` | — |
| **Redis** (сессии, комнаты, лидерборды, lease) | `redis-development:redis-core` | `redis-connections`, `redis-clustering`, `redis-security`, `redis-observability` |
| **Тесты** | `/tdd` (агент `tdd-guide`) + `backend-design:testing-with-discernment` | агент `pr-review-toolkit:pr-test-analyzer` |
| **Фронтенд: UI и визуал** | `frontend-design:frontend-design` | ui-ux-pro-max (если доступен) |
| **E2E фронта** | playwright (MCP) | — |
| **Типы и доменные модели** | — | агент `pr-review-toolkit:type-design-analyzer` |
| **Комментарии и докстринги** | правила §4 | агент `pr-review-toolkit:comment-analyzer` |
| **После написания кода** | `/review` (агент `code-reviewer`; для чувствительных зон + `security-reviewer`) | агент `code-simplifier` |
| **Перед коммитом / PR** | `/verify pre-pr` | `pr-review-toolkit:review-pr` для крупных PR |
| **Красные проверки (lint, типы, контракты, тесты)** | `/build-fix` (агент `build-error-resolver`) | — |
| **Документация после изменений** | `/update-docs` (агент `doc-updater`) | агент `pr-review-toolkit:comment-analyzer` |
| **Уборка мёртвого кода** | `/refactor-clean` (агент `refactor-cleaner`) | — |
| **Конец этапа работы, длинная сессия** | `/checkpoint` → `/compact` (скилл `strategic-compact`) | `/learn` для неочевидных решений |
| **Прод сломался / медленно** | `backend-design:debugging-discipline` | агент `backend-design:incident-investigator` |

## 3. Безопасность: обязательные проверки

Security Guidance работает автоматически: хуки на каждой правке и при завершении. Поверх этого по зонам:

| Зона | Перед merge обязательно |
|---|---|
| **Любой diff** | `backend-design:security-discipline` |
| **Новый endpoint или права доступа** | `backend-design:auth-and-authorization` + агент `backend-design:security-reviewer` |
| **identity, auth, токены, сессии, роли** | всё выше + `finecomb:finecomb` по изменённому модулю |
| **payments, progression (ledger), entitlements** | всё выше + `finecomb:finecomb` + `backend-design:idempotency-and-side-effects`. Ручная проверка владельцем проекта |
| **Изменение OpenAPI контракта** | `42crunch-api-security-testing:42crunch-audit` по спецификации |
| **Перед релизом** | `finecomb:finecomb` по всему репозиторию + 42crunch audit + scan на staging |
| **Перед включением биллинга** (`billing.enabled=true`) | Всё выше + внешний аудит или пентест платёжного контура. Ни один плагин его не заменяет |

## 4. Комментарии и автодокументация в коде

- **Докстринги** (Google style) обязательны для: публичных классов и функций `domain` и `application`, портов, плагинов game-kernel, публичных функций `libs/*`. Докстринг объясняет **что делает и какие инварианты держит**, а не пересказывает код.
- **Комментарии в коде** — только «почему»: неочевидное решение, ссылка на ADR (`# см. ADR 0010: single-writer`), обход бага библиотеки со ссылкой на issue.
- Запрещены комментарии, которые дублируют код, и закомментированный код.
- Язык докстрингов и комментариев — **русский**, идентификаторы — английский.
- API-документация генерируется из кода (OpenAPI из FastAPI, `description` у полей Pydantic). Справочник libs — `mkdocs` + `mkdocstrings` из докстрингов (этап 0).
- После больших правок документации — агент `pr-review-toolkit:comment-analyzer`.

## 5. Проектные скиллы

| Скилл | Когда |
|---|---|
| `guessverse-conventions` | Любая работа с кодом Guessverse: структура сервиса, слои, именование, события, тесты |
| `guessverse-new-service` | Создание сервиса или библиотеки по шаблону |
| `strategic-compact` | Когда и как сжимать контекст, не теряя суть задачи |
| `learned-*` | Знания, сохранённые командой `/learn` (после подтверждения пользователя) |

Копии проектных скиллов лежат в `.claude/skills/` — источник истины в репозитории. Если те же скиллы сохранены в аккаунте claude.ai, держите одну копию, чтобы описания не дублировались в контексте.

Скиллы и `CLAUDE.md` обновляются в том же PR, что и правила, которые они описывают.

## 6. Чего не делать

- Не запускать все ревью-агенты подряд на мелкий diff: для правки в 20 строк достаточно `security-discipline` + `code-simplifier`.
- Не запускать параллельно ревьюеров с одной задачей: `/review` (проектный), `code-review:code-review` и `pr-review-toolkit:review-pr` — выбрать один.
- Не обходить хуки (`--no-verify`, правка сгенерированных файлов): исправлять причину.
- Не принимать технологию, которой нет в [architecture/README.md](architecture/README.md) §6, без ADR.
