---
description: "Цепочка агентов для сложной задачи: feature | bugfix | refactor | security | architecture."
argument-hint: "<тип> <задача>"
---

Оркестрация: $ARGUMENTS

> Дорого: каждый агент стартует с нуля. Только для крупных задач; мелкие шаги цепочки делай в основной сессии.

Цепочки:
- **feature:** planner → (подтверждение пользователя) → tdd-guide → code-reviewer → security-reviewer (если чувствительная зона) → doc-updater
- **bugfix:** tdd-guide (воспроизводящий тест) → code-reviewer
- **refactor:** architect → planner → (подтверждение) → refactor-cleaner/tdd-guide → code-reviewer
- **security:** security-reviewer → code-reviewer → architect (если нужны изменения архитектуры)
- **architecture:** architect → planner → (подтверждение) → doc-updater (ADR)

Между агентами передавай короткую сводку:
```
## ПЕРЕДАЧА: <агент> → <агент>
Сделано / Решения / Изменённые файлы / Открытые вопросы
```
Ревьюер один: для чувствительных зон — `security-reviewer`, иначе — `code-reviewer`.
В конце — общий отчёт: что сделано, файлы, результаты тестов и проверок, что осталось.
