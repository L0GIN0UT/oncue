# Проход по техдолгу — передача

## Состояние (2026-10-25)
Группа A закрыта двумя PR (метка `e2e`):
- [#35](https://github.com/L0GIN0UT/oncue/pull/35) `techdebt/seed-e2e` — идемпотентный повторный `gv dev seed`.
- [#36](https://github.com/L0GIN0UT/oncue/pull/36) `techdebt/frontend-noise` — шум фронта, `ranked.spec`, `adminContext`, таймаут `waitFor`.

Группа B (безопасность) — [#38](https://github.com/L0GIN0UT/oncue/pull/38), `techdebt/security` (метка `e2e`); зона повышенной безопасности. Отдельно [#37](https://github.com/L0GIN0UT/oncue/pull/37) — `fix/e2e-events-final` (финал события ждёт рамку опросом JSON инвентаря, а не перезагрузкой `/shop`; флейк 429 на `realtime/token`).

Группы C (ops), D (product), E (minor) — не начаты; по одной за сессию.

## Решения без владельца
- Контракт bff `/pages/shell` не менялся: он уже отдаёт `bell`, `sanctions`, `event`. Presence — мутация (POST social), а bff только читает (D4), поэтому «один ответ» не делался; шум снят на фронте.
- Presence — Web Locks (`gv-presence`), не BroadcastChannel: замок сам переходит к другой вкладке при закрытии держателя. Без Web Locks — прежнее поведение.
- `asyncUtilTimeout` 5 с глобально в vitest-setup: причину падений `GameScreen.modes`/`CollectionEditor` локально воспроизвести не удалось (дефолт 1 с под нагрузкой — наиболее вероятная).
- `party.spec` как файла нет (сценарии party — в `e2e/online.spec.ts`); без воспроизведения не менялся, строка осталась в бэклоге.
- `modes.spec.ts` имеет свой `adminContext` с другой сигнатурой (токен снаружи) — не трогал.

### Группа B — решения без владельца
- Disconnect делает **match**, не identity: ключ server API Centrifugo остаётся только у публикующих сервисов (D9). Бан `ACCOUNT` — по событию `UserSanctionsChanged.account_banned`, код 3500 (без переподключения); кик из комнаты — код 3000. Остаточный риск (Centrifugo OSS не отзывает токены, окно ≤ `token_ttl_s`) — ADR 0059 и строка бэклога; закрывает только connect/subscribe proxy.
- Провал `disconnect` при бане не бросает исключение: иначе событие откатит проекцию санкций и бан `ONLINE` не применится. Вместо этого метрика `gv_match_disconnect_failed_total` (завести алерт в группе C вместе с лагом consumer social).
- Иерархия: допустимо, если права цели ⊆ прав актёра (как `RoleMatrix.can_grant`); равный модератор допустим, admin — нет. Проекция `staff_perms_proj` из `UserRolesChanged`; бэкфилл — `GV_REANNOUNCE_STAFF_PERMS=true` на одну выкладку identity `migrate`.
- Роль БД: рантайм `progression_app` без прав на изменение журналов; журналы определяются по триггеру `progression_ledger_forbid_mutation` (новый журнал закрывается сам). Выдача прав — каждый `migrate`, нет роли — ошибка. Локально на старом томе: `gv dev db-roles`.
- `p/*`: предпросмотр только для `pending`; у одобренной картинки в карточке admin превью нет (метаданные). Если нужно — отдавать `approved` потоком из `u/`.
- trivy: SARIF сохраняется артефактом job, не выгружается в Code Scanning (не нужен GitHub Advanced Security); блокировка только по CRITICAL (`ignore-unfixed`), HIGH остаётся отчётным (ADR 0057).
- Кик: повторы `unsubscribe`/`disconnect` с паузой остаются в цикле владельца комнаты (как у `unsubscribe` раньше); при недоступном Centrifugo это замедляет комнату — вынос в фон в группе C.
- Preflight секретов Centrifugo не проверяется внутри сервисов (identity и match видят только свой секрет): `gv dev up` и `gv preflight` для конвейера выкладки.
- Падения тестов match (2) при параллельной нагрузке прошли повторным прогоном (258 passed).

## Чек-лист владельцу
- После слияния #35: `gv dev seed` повторно на засеянном стенде — без падения на 409.
- После слияния #36: на живом стеке в двух вкладках проверить, что `POST /v1/me/presence` идёт из одной (видимой); колокольчик показывает число без отдельного `unread-count` на старте.

## Дальше
Группа B по тому же промпту (`techdebt/security`, security-reviewer обязателен, review-migration на миграции).
- После слияния #38 (требуется ручная проверка владельцем): выкладка в порядке — роль `progression_app` (`gv dev db-roles`) → identity с `GV_REANNOUNCE_STAFF_PERMS=true` → social `0006` → снять флаг → media-pipeline → admin. Проверить: модератор не может санкционировать admin; бан `ACCOUNT` рвёт открытое соединение; превью pending-аватарки в карточке UGC открывается; `/media/p/…` отвечает 404; `progression_app` не может `UPDATE xp_ledger`.
