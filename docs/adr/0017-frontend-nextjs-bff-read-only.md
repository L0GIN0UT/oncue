# 0017. Фронтенд: Next.js в `frontend/`, bff только чтение

- **Статус:** принято
- **Дата:** 2026-10-11

## Контекст
На этапе 2 появляется веб-клиент: главная, соло-хаб, игровой экран, ревил и итог. Нужно выбрать стек, место в монорепо, способ рендеринга и путь запросов браузера. Игровой ход должен укладываться в p95 < 150 мс; refresh-cookie identity живёт на своём пути, а повторное использование refresh отзывает всю сессию. Решения D1, D2, D4 [плана этапа 2](../plans/stage-2.md#1-сквозные-решения-утверждены).

## Решение
**Стек и место (D1).** Next.js (App Router, версия зафиксирована в `frontend/pnpm-lock.yaml`), React 19, TypeScript `strict`, pnpm, Node 24 LTS; Tailwind CSS v4 + shadcn/ui, тексты — next-intl. Каталог `frontend/` — одно приложение без JS-workspace. Сборка `output: "standalone"`, контейнер `web` за gateway (`location /`, свой compose-файл, в `infra/services.toml` не входит).

**Рендеринг (D2).** SSR (server components) — каркас, главная и хаб: публичные данные берутся у bff по внутреннему адресу. CSR — личный статус и весь игровой экран (TanStack Query, `no-store`). Обновление сессии — только в браузере, single-flight внутри вкладки и между вкладками (Web Locks), чтобы не сработало обнаружение повторного использования refresh.

**Путь запросов (D4).**

| Откуда | Куда | Что |
|---|---|---|
| браузер | game `/api/game/v1` | старт, ход, пропуск, сдача, ассет уровня (cookie + CSRF, `Idempotency-Key`) |
| браузер | catalog `/api/catalog/v1/autocomplete` | автокомплит ответа |
| браузер, SSR | bff-web `/api/web/v1` | GET-агрегаты экранов |
| браузер | identity `/api/identity/v1` | вход, регистрация, гость, refresh |

bff-web — только чтение: мутаций нет, CSRF и идемпотентность ему не нужны; он stateless, без БД (Redis — кэш публичных данных).

**Заголовки безопасности.** CSP с nonce на каждый ответ-страницу ставит сам Next (`frontend/src/proxy.ts`): `script-src 'self' 'nonce-…' 'strict-dynamic'`, `object-src 'none'`, `frame-ancestors 'none'`, `connect-src 'self'`; `'unsafe-eval'` и `ws:` — только в dev-сервере. Остальные заголовки (HSTS, `X-Frame-Options`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`) — gateway (`infra/nginx/snippets/web-headers.conf`).

**Dev-прокси.** Переписывание `/api/*` и `/media/*` на `GV_DEV_API_ORIGIN` в обход gateway включается **только** при `GV_E2E=1` (Playwright на моках). Переменная без флага — ошибка старта (`instrumentation.ts`, код выхода 64): иначе забытая переменная в проде отправила бы cookie сессии на посторонний адрес.

## Альтернативы
- **`apps/web` + pnpm workspace.** Нужен, когда появится второй клиент (Telegram Mini App); переезд механический.
- **Vite SPA.** Проще, но закрывает SSR и SEO этапа 7 и даёт медленную первую отрисовку.
- **Полный SSR.** Требует обновлять токен на сервере Next, а refresh-cookie живёт на пути identity.
- **Все запросы через bff.** Лишний хоп на ходе, дублирование контрактов, CSRF и идемпотентность пришлось бы делать в bff.
- **CSP в nginx.** Статическая политика без nonce требует `'unsafe-inline'` для скриптов Next.

## Последствия
- (+) Ход игры — один хоп gateway → game; bff остаётся простым агрегатором.
- (+) CSP без `'unsafe-inline'` для скриптов.
- (−) Браузер знает о трёх-четырёх бэкендах; их контракты фронт держит в `frontend/src/shared/api/schema`.
- (−) Страницы с CSP-nonce не кэшируются целиком на gateway.
- Новый экран: публичные данные — через bff (SSR), команды — напрямую в доменный сервис.
