# Проход по техдолгу — передача

## Состояние (2026-10-25)
Группа A закрыта двумя PR (метка `e2e`):
- [#35](https://github.com/L0GIN0UT/oncue/pull/35) `techdebt/seed-e2e` — идемпотентный повторный `gv dev seed`.
- [#36](https://github.com/L0GIN0UT/oncue/pull/36) `techdebt/frontend-noise` — шум фронта, `ranked.spec`, `adminContext`, таймаут `waitFor`.

Группы B (security), C (ops), D (product), E (minor) — не начаты; по одной за сессию.

## Решения без владельца
- Контракт bff `/pages/shell` не менялся: он уже отдаёт `bell`, `sanctions`, `event`. Presence — мутация (POST social), а bff только читает (D4), поэтому «один ответ» не делался; шум снят на фронте.
- Presence — Web Locks (`gv-presence`), не BroadcastChannel: замок сам переходит к другой вкладке при закрытии держателя. Без Web Locks — прежнее поведение.
- `asyncUtilTimeout` 5 с глобально в vitest-setup: причину падений `GameScreen.modes`/`CollectionEditor` локально воспроизвести не удалось (дефолт 1 с под нагрузкой — наиболее вероятная).
- `party.spec` как файла нет (сценарии party — в `e2e/online.spec.ts`); без воспроизведения не менялся, строка осталась в бэклоге.
- `modes.spec.ts` имеет свой `adminContext` с другой сигнатурой (токен снаружи) — не трогал.

## Чек-лист владельцу
- После слияния #35: `gv dev seed` повторно на засеянном стенде — без падения на 409.
- После слияния #36: на живом стеке в двух вкладках проверить, что `POST /v1/me/presence` идёт из одной (видимой); колокольчик показывает число без отдельного `unread-count` на старте.

## Дальше
Группа B по тому же промпту (`techdebt/security`, security-reviewer обязателен, review-migration на миграции).
