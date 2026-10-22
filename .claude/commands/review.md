---
description: "Ревью текущих изменений агентом code-reviewer (и security-reviewer для чувствительных зон)."
argument-hint: "[путь или ветка]"
---

Изменённые файлы:
!`git diff --name-only HEAD; git diff --name-only --cached`

Изменения: «$ARGUMENTS» (если пусто — рабочая копия). Один ревьюер:
- затронуты `services/identity`, `services/payments`, `services/progression`, `libs/auth`, `infra/nginx` — агент **security-reviewer** (он же проверяет слои и тесты);
- иначе diff до ~100 строк — ревью в основной сессии по чек-листу агента `code-reviewer`; больше — агент **code-reviewer**.

Итог: можно мержить / после правок / блок.
