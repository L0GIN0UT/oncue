# `.claude/` — настройка Claude Code для Guessverse

Claude Code подхватывает эту папку автоматически, когда открыт корень репозитория. Подробно, что когда использовать, — в `docs/10-ai-assistant.md`.

```
.claude/
  settings.json     # хуки и права (общие для команды, в git)
  settings.local.json  # личные настройки (НЕ в git)
  agents/           # 9 агентов, знающих суть проекта (модель, effort, бюджет ответа — docs/10-ai-assistant.md §0.1)
  commands/         # 10 команд: /plan /tdd /review /verify /build-fix /refactor-clean /update-docs /checkpoint /learn /orchestrate
  rules/            # правила, подгружаемые по путям файлов (paths:), workflow.md — всегда
  skills/           # guessverse-conventions, guessverse-new-service, strategic-compact, learned-*
  sessions/         # заметки сессий (НЕ в git): пишут хуки и /checkpoint
```

## Как пользоваться

```text
/plan добавить вход через Яндекс ID в identity     → план, ждёт вашего «да»
/tdd шаг 1 плана                                    → тест падает → код → тест зелёный
/review                                             → ревью diff (+ безопасность для identity/payments/...)
/verify pre-pr                                      → линтеры, тесты, контракты, docs, безопасность
/checkpoint вход-яндекс                             → заметка для следующей сессии, предложение коммита
/compact сохрани: ...                               → сжатие контекста на границе этапа
/orchestrate feature <задача>                       → вся цепочка агентов с передачей результатов
```

Агентов можно звать и напрямую: «используй агента architect, чтобы оценить …».

## Хуки

Команды в `settings.json` вызывают `uv run --no-sync gv hook <имя>`; логика — `tools/gv/src/gv_cli/hooks.py`, тесты — `tools/gv/tests/test_hooks.py`. Нужен установленный `uv` и выполненный `uv sync --all-packages`. Сбой хука никогда не блокирует работу — только пропускается.

Временно отключить хуки для себя: в `.claude/settings.local.json` — `{"disableAllHooks": true}`.

## Происхождение

Основано на [everything-claude-code](https://github.com/WorldFlowAI/everything-claude-code) (форк/копия [affaan-m/everything-claude-code](https://github.com/affaan-m/everything-claude-code), лицензия MIT по README проекта). Что изменено при адаптации:

| В оригинале | Здесь | Почему |
|---|---|---|
| JS/TS-хуки: Prettier, tsc, проверка `console.log`, tmux-напоминания | ruff fix+format, проверка `print`/`breakpoint`, защита сгенерированных файлов, контрактов, `.env`, опасных git/docker-команд | Бэкенд на Python; реальные риски проекта — правка генерируемого и обход проверок |
| Условия хуков `tool == "Bash" && ...` | `matcher` по имени инструмента + логика в коде | В Claude Code `matcher` — шаблон имени инструмента |
| Хук блокирует создание любых `.md` | убран | Документация проекта — набор `.md` в `docs/` и ADR |
| Хуки — однострочники `node -e` | Python-модуль `gv hook` с тестами | Тестируемость, один язык со всем инструментарием |
| Заметки сессий и выученные скиллы в `~/.claude` | `.claude/sessions/` и `.claude/skills/learned-*` в проекте | Знания о проекте живут с проектом |
| Агенты-универсалы (JS-примеры, «80 % покрытия», immutability через spread) | Агенты с сутью Guessverse, слоями, outbox, контрактами, порогами из `docs/04-testing.md` | Без контекста проекта агенты дают общие советы |
| `e2e-runner`, `/e2e`, `/setup-pm`, `/eval`, `eval-harness`, `clickhouse-io`, `frontend-patterns`, MCP-конфиги | не перенесены | Фронта и аналитики ещё нет; MCP — по необходимости. Вернуть на этапах 2 и 7 |
| Правила для всех файлов сразу | `paths:` — только для затронутых | Экономия контекста |
