# Handoff: одноразовые коды по почте и доверенные устройства (identity)

> Как читать: новая сессия Claude Code начинается с этого файла. Правила работы — `CLAUDE.md`, `.claude/rules/workflow.md`, `docs/10-ai-assistant.md` §0.1 (экономия токенов). Проект на claude.ai отсюда не виден — всё нужное здесь.

## Состояние
- Этап 4a влит в `main`; `chore/claude-token-economy` влита (PR 20).
- Ветка `feat/identity-email-codes` (PR в `main`): коды из писем, доверенные устройства, ADR 0041, миграции
  `0009`–`0012`, фронтенд, e2e, шаблоны compose. Проверено: `gv lint`, `gv test identity` (unit,
  integration, component), `tools/gv`, vitest/tsc/eslint, e2e (collections, duel, lobby, party) на локальном
  стеке; ревью безопасности (`security-reviewer`) и ревью схемы пройдены, блокеры исправлены. Нужна ручная
  проверка владельцем (зона identity).
- Следующая задача после этого PR — этап 4b (ADR 0037–0040 зарезервированы); хвосты — в разделе «Доработки по ревью».

## Доработки по ревью (выполнены в этом же PR)
Из «принятых рисков» ADR 0041 исправлены: тайминг `forgot` (письма фоном), лимит писем при `resend` у
пустышки (`mail_key`, миграция `0012`), привязка кода к браузеру (cookie `gv_bind`), порядок проверок до
списания попытки. Остаётся на будущее: надёжная доставка писем через outbox/воркер; повторный вход после
сброса пароля для привилегированных ролей.

## Правила, которые нельзя нарушать
- Коммиты и пуши только от `L0GIN0UT <127785212+L0GIN0UT@users.noreply.github.com>`. Без `Co-Authored-By`, без упоминаний Claude. Коммит только после «да» владельца.
- Без force-push; хуки не обходить; `.env` не читать.
- identity — зона повышенной безопасности (`docs/10-ai-assistant.md` §3): перед merge `security-reviewer`, `finecomb` по изменённым модулям, `review-migration`; в отчёте — «требуется ручная проверка владельцем».
- Контракты в `contracts/` меняются первыми; сгенерированные файлы (`contracts/openapi/*.json`, `frontend/src/shared/api/schema/*`, `infra/compose/*`, `.env.example`) — только через `gv contracts export`, `pnpm gen:api`, `gv gen`.

## Что решено владельцем
- Ссылки с токенами в URL убрать полностью (`verify_link`, `reset_link`, таблица `email_tokens`). Только коды. Прод-пользователей нет, миграционный период не нужен.
- Коды для: подтверждения регистрации; входа с нового устройства; «забыл пароль»; смены пароля в профиле.
- По умолчанию (всё в конфиге `GV_EMAIL_CODE__*`): 6 цифр, TTL 600 с, ≤ 5 попыток, повтор не раньше 60 с, лимит писем на адрес (есть `EmailLimitSettings` 5/час) и per-IP лимиты; один активный код на (субъект, назначение); в БД только HMAC; `hmac.compare_digest`; одинаковый ответ для существующего и несуществующего адреса.
- Доверенное устройство: cookie `gv_device` (HttpOnly, Secure, SameSite=Lax, 180 дней, в БД sha256). Неизвестное устройство → `code_required`, сессия только после кода.
- Смена пароля отзывает сессии кроме текущей; сброс — все сессии и устройства.
- SMTP: `SmtpEmailSender` (aiosmtplib) и `GV_EMAIL__*`/`GV_SMTP__*` уже есть в `services/identity/src/identity_service/settings.py` и `infrastructure/clients.py`, но не прокинуты в compose/`.env.example` — добавить через шаблоны `tools/gv/src/gv_cli/templates/gen/*.j2`. Локально и в CI остаётся `backend=log`, e2e читает код из лога по `challenge_id`.
- Найденная попутно проблема: `LogEmailSender` пишет секреты в лог и на staging (`wiring.py`, `include_links=not is_production`) — оставить только для local/ci.
- `fill_secrets` в `tools/gv/src/gv_cli/main.py` подставляет случайную строку в любую пустую переменную с `PASSWORD` в имени — для `GV_SMTP__PASSWORD`/`USERNAME` нужно исключение.

## Открытые вопросы (спросить владельца до кода)
1. Подтверждение кода регистрации сразу выдаёт сессию и доверие устройству? (предложение — да)
2. Дневной потолок неудач на субъекта — 20? Lockout-DoS (чужие неудачи блокируют сброс жертве на сутки) принимаем?
3. `logout-all` и смена пароля снимают доверие с других устройств? (предложение — да, текущее остаётся)
4. Admin-вход и вход через Яндекс без проверки устройства — принять как риск?
5. Удаление `email_tokens` (`0010`) — в этой же ветке или отдельным релизом (expand/contract)?
6. Ломающая правка `/v1` без `/v2` — подтвердить исключение из правила 6, записать в ADR.
7. Добавить `GV_SMTP__USE_TLS` (порт 465) и `GV_SMTP__TIMEOUT_S`?
8. `backend=log` в production — ошибка старта?

## План (кратко; номер ADR — 0041, 0037–0040 зарезервированы под 4b)
Контракт `/v1/auth/*`: `register` → 202 `ChallengeResponse{challenge_id, purpose, code_length, expires_in_s, resend_after_s}`; `verify-email` `{challenge_id, code}` → сессия; `login` → `authenticated | code_required`; новые `login/confirm`, `codes/resend`, `password/change`, `password/change/confirm`; `password/forgot` → `ChallengeResponse`; `password/reset` `{challenge_id, code, password}`. Ошибки `auth.code_invalid` (`attempts_left`), `auth.code_expired`, `auth.code_resend_too_early` (+`Retry-After`), `auth.code_limit_exceeded`. События Redpanda не меняются.

БД: `email_challenges` (purpose, subject_hash, user_id NULL, code_hash, attempts/max_attempts, send_count, device_hash, session_id, expires/consumed/cancelled_at; частичный UNIQUE активного кода) и `trusted_devices` (user_id, device_hash, expires_at, revoked_at; UNIQUE). Миграции `0009` (создать), `0010` (удалить `email_tokens`).

Ключевые решения: попытка списывается **до** сравнения и в отдельной транзакции (иначе откат при ошибке даёт бесконечный перебор); погашение условным UPDATE по rowcount; для несуществующего адреса — «пустышка» с тем же ответом; подавленное письмо не создаёт challenge; дневной потолок неудач; код — `secrets.randbelow`.

Порядок: Ф0 контракт + ADR + порты + settings → параллельно Ф1-A домен/application (unit, hypothesis), Ф1-B инфраструктура/миграции (integration), Ф1-C шаблоны gen + `fill_secrets` (`tools/gv/tests/test_gv.py`), Ф1-D фронт (`frontend/src/features/auth/`, `CodeStep.tsx`, `ru.json`, удалить `app/verify-email`), Ф1-F docs → Ф2 wiring + API + component-тесты → Ф3 e2e (`e2e/auth.ts` хелпер `codeFor(challenge_id)`, заменить `usedVerifyTokens` в `e2e/online.ts`, `e2e/collections.spec.ts`).

Файлы: `services/identity/src/identity_service/{application/commands/auth.py, application/ports/__init__.py, settings.py, wiring.py, infrastructure/clients.py, infrastructure/db/{tables.py,repositories.py}, api/public/__init__.py}`, `tools/gv/src/gv_cli/{main.py, templates/gen/*.j2}`, `frontend/src/features/auth/AuthForms.tsx`, `frontend/messages/ru.json`, `e2e/online.ts`, `e2e/collections.spec.ts`.

Проверки перед PR: `gv lint`, `gv test identity` (все уровни), `pytest tools/gv/tests`, `pnpm test`, `pnpm gen:api:check`, `gv contracts check --base origin/main`, `gv gen --check`, `mkdocs build --strict`, e2e локально (скрипт владельца `Проекты\oncue-stage4a.sh`).

## Как вести работу экономно
- Агентов — минимум: Ф0 и Ф2 в основной сессии; Ф1-A/B/D — по одному `tdd-guide` на поток, не больше трёх параллельно; `explorer` для поиска; один ревьюер (`security-reviewer`) в конце.
- `/checkpoint` в конце каждого потока; этот файл обновлять по ходу (раздел «Состояние»).