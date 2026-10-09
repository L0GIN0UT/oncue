---
paths:
  - "infra/**"
  - ".github/**"
  - "tools/gv/**"
---
# Инфраструктура и CI

- Состав сервисов — только `infra/services.toml`. Сгенерированные файлы (compose.services*, nginx/generated, prometheus/services.yml, .env.example, .importlinter, скрипт БД) меняются через шаблоны в `tools/gv/src/gv_cli/templates/gen/` + `uv run gv gen`.
- Всё должно переноситься в Kubernetes без правки кода: env-конфиг, stateless, health-пробы, SIGTERM, логи в stdout (`docs/07-infrastructure.md` §2).
- Секреты — только через `.env` (не в git) или секреты платформы. Не читать и не печатать содержимое `.env`.
- Новый компонент инфраструктуры — сначала обоснование (`backend-design:boring-by-default`), при необходимости ADR.
