---
paths:
  - "contracts/**"
  - "**/api/**"
  - "**/infrastructure/messaging/**"
---
# Контракты API и событий

- Contract-first: сначала контракт, потом реализация.
- `contracts/openapi/*.json` и `contracts/events/envelope.v1.json` — снимок из кода: правка кода → `uv run gv contracts export`. Руками не редактировать (хук заблокирует).
- Схемы доменных событий `contracts/events/<service>/<Event>.v<N>.json` пишутся руками. Добавить необязательное поле — можно; удалить/переименовать/сменить тип/сделать обязательным — только новой версией `v<N+1>`, старую публиковать параллельно.
- Публичный API — `/v1`, внутренний — `/internal/v1`, админский — `/admin/v1`. Ошибки — RFC 9457 с `code`; пагинация курсорная; мутации с `Idempotency-Key`.
- Проверка: `uv run gv contracts check --base origin/main`.
