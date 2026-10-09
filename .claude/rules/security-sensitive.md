---
paths:
  - "services/identity/**"
  - "services/payments/**"
  - "services/progression/**"
  - "libs/auth/**"
  - "infra/nginx/**"
---
# Зона повышенной безопасности

Изменения здесь затрагивают аккаунты, деньги, валюту или границу системы.

- Перед завершением: агент **security-reviewer** по diff + скилл `backend-design:idempotency-and-side-effects` для операций с балансом и платежами; для identity/payments — `finecomb`.
- Деньги и валюта: целые копейки, append-only ledger, идемпотентность по `opId`, баланс не уходит в минус, вебхуки — проверка подписи + дедупликация.
- Права проверяются по `perm` (`require_perm`), владение ресурсом — в application-слое.
- Биллинг выключен (`billing.enabled=false`, план `free_all`) — не включать без решения владельца.
- В отчёте пользователю явно отметить: «требуется ручная проверка владельцем проекта».
