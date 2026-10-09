---
description: "Полная проверка состояния репозитория перед коммитом или PR."
argument-hint: "[quick|full|pre-pr]"
---

Проверка репозитория. Режим: «$ARGUMENTS» (если пусто — full).

Состояние:
!`git status --short`

Выполни по порядку и остановись на первом критическом провале:
1. **quick:** `uv run gv lint` (ruff, mypy --strict, import-linter, gen --check, contracts check).
2. **full:** + `uv run gv test --changed --base origin/main` (если нет origin — `uv run gv test`).
3. **pre-pr:** + `uv run mkdocs build --strict` + `uv run gv contracts check --base origin/main` + агент **security-reviewer** по diff, если затронуты identity, payments, progression, libs/auth или infra/nginx.
4. Поиск отладочного кода: `print(`/`breakpoint()` в `services/*/src` и `libs/*/src`.

Отчёт:
```
ПРОВЕРКА: ✅ / ❌
Линтеры:     OK / N проблем
Тесты:       X/Y, покрытие Z %
Контракты:   OK / устарели / ломающие изменения
Документация: OK / ошибки
Безопасность: OK / находки
Готово к PR: да / нет — что исправить
```
