---
description: "Ревью текущих изменений агентом code-reviewer (и security-reviewer для чувствительных зон)."
argument-hint: "[путь или ветка]"
---

Изменённые файлы:
!`git diff --name-only HEAD; git diff --name-only --cached`

1. Агент **code-reviewer** — по изменениям: «$ARGUMENTS» (если пусто — рабочая копия).
2. Если затронуты `services/identity`, `services/payments`, `services/progression`, `libs/auth`, `infra/nginx` или новые/изменённые ручки — параллельно агент **security-reviewer**.
3. Сведи результаты в один список по серьёзности, без дублей. Итог: можно мержить / после правок / блок.
