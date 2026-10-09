---
name: "guessverse-new-service"
description: "Use when creating a new service or shared library in the Guessverse monorepo, so it matches the standard template, contracts, CI and docs."
---

# Guessverse: create a service or lib

Follow `guessverse-conventions` for all rules. This skill is the checklist.

## Before code
1. Confirm the service is justified by the split criteria in `docs/architecture/services.md` (load profile, state type, cost of failure, change rate / external deps). If not, make it a module of an existing service.
2. If it changes architecture, write an ADR in `docs/adr/` from `0000-template.md`.
3. Add or confirm its card in `docs/architecture/services.md`: responsibility, data, published and consumed events, scaling, degradation.

## Scaffold
1. Prefer `gv new-service <name>` once it exists. Otherwise create the standard hexagonal layout manually (see conventions skill).
2. Required from the first commit:
   - `settings.py` (pydantic-settings, env only), `main.py` with `ServiceDefinition` (composition root, ADR 0011)
   - entrypoints: api, consumer, worker, relay, all
   - `/health/live`, `/health/ready`, `/metrics`; structlog JSON logs; OpenTelemetry
   - outbox + inbox tables and relay from `libs/messaging`
   - alembic setup with its own database and DB user
   - Dockerfile (one image, several commands)
   - `services/<name>/CLAUDE.md` (10-15 lines: purpose, owns, events in/out, gotchas)
3. Contracts first: `contracts/openapi/<name>.yaml` and `contracts/events/<name>/*.json`.
4. Tests: `tests/unit|integration|component|contract` using `libs/testkit`; one smoke test per level.
5. Wiring: add to `infra/compose/compose.services.yml` with resource limits and healthcheck, to Nginx routing if public, to CI matrix, to `docs/README.md` if a new doc is added.

## For a lib (`libs/<name>`)
- Only infrastructure or shared engine code, never domain models of a service.
- Public API with Russian docstrings (mkdocstrings), changelog, semver; breaking change = major.

## Done when
- `gv test <name>` green on all levels, import-linter green, contracts check green.
- Service starts in compose, health ready, a trace passes through it.
- Docs and CLAUDE.md updated in the same change.