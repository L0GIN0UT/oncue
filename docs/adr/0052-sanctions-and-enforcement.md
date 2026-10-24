# 0052. Санкции и их применение

- **Статус:** предложен (черновик; решения владельца — 2026-10-24)
- **Дата:** 2026-10-24

## Контекст
Санкции `RANKED`, `CHAT`, `ONLINE`, `ACCOUNT` выдаёт модератор вручную со сроком или бессрочно. В claim
`sanctions` identity сейчас всегда пишет 0; Nginx JWT не проверяет, поэтому gateway применить санкции не может.

## Решение
- **Источник правды — social** (`sanctions`: kind, срок, причина, `actor_id`, `revoked_*`; история append-only).
  Любое изменение публикует `UserSanctionsChanged` (полная активная маска, `version`, ближайший `expires_at`,
  `account_banned`). Просроченные снимает периодическая задача relay (под advisory lock) с тем же событием.
- **Биты claim:** `RANKED=1`, `CUSTOM_CONTENT=2` (ADR 0030), `CHAT = CHAT_MUTE(8) | COMMENTS(16)`,
  `CHAT_SLOW(4)` — резерв, модератор не выдаёт, `ONLINE=32` — новый. `ACCOUNT` не claim: флаг `account_banned`.
- **identity:** хранит `user_sanctions` из событий (порядок по `version`), пишет маску в токен при выпуске;
  `ACCOUNT` → вход 403 `auth.account_banned`, отзыв сессий и refresh-токенов.
- **match и social:** проекция `sanctions_proj`; проверка `claim | proj` на rooms, очередях, приглашениях, чате;
  снятие — по проекции (claim устаревает ≤ access TTL, 15 мин). По событию match выбивает игрока из очереди и лобби,
  вызывает Centrifugo `unsubscribe` с повтором; подписочный токен комнаты при продлении перепроверяет санкцию
  (закрывает хвост 4a).
- **gateway санкции не применяет** (решение владельца «match и gateway» уточнено: Nginx не разбирает JWT).
- **Правила:** модератор не выдаёт и не снимает санкции себе; progression без изменений; права
  `social.moderate`, `users.sanction`, `comments.delete_any`.

## Уточнения 5c
Эффективная маска в match — **проекция `sanctions_proj`, если у игрока есть её запись, иначе claim** (не
`claim | proj`): проекция обновляется событием за секунды и поэтому авторитетна для снятия, claim живёт до
access TTL; маска считается на момент запроса по `active[].expires_at` (истёкшие не входят), identity
хранит те же `active` в `user_sanctions`. В social действует объединение claim и таблицы `sanctions`
(приглашения). Одна не снятая запись на `(игрок, вид)`: повторная выдача закрывает прежнюю как `replaced`
(истёкшую, но не закрытую задачей — как `expired`); `version` растёт под блокировкой строки
`sanction_state` в транзакции изменения. `ACCOUNT` в identity: вход, refresh и выпуск токена — 403
`auth.account_banned` (после проверки пароля, без причины), событие с `account_banned` отзывает все сессии
и пишет `sid` в denylist. По `ONLINE` match шлёт владельцу комнаты системную команду `evict`, снимает
игрока с очереди и отзывает подписку Centrifugo (до `GV_MATCH__UNSUBSCRIBE_ATTEMPTS` попыток, метрика
`gv_match_unsubscribe_failed_total`); subscription-токен при выдаче и продлении перепроверяет `ONLINE`.

Отказоустойчивость match: вход в онлайн (`POST /rooms`, `join`, `rematch`, `enter` очереди, `realtime-token`)
при недоступной проекции — fail-closed, 503 `match.sanctions_unavailable`; команды игрока, уже сидящего в
комнате, деградируют до claim; оба случая считает метрика `gv_match_sanctions_projection_failures_total`.
При `ONLINE` закрыты все команды комнаты, кроме `leave` и админского `abort`; после `join` проверка
повторяется (гонка «вход — бан»), забаненного выбивает системная команда `evict`. Причины закрытия
записей системой имеют префикс `system:` (`system:replaced`, `system:expired`), модератору он запрещён
(422 `social.reason_reserved`); снятие бана `account` требует `social.sanctions.account`.

Принятые ограничения (без кода):
- Гонка входа и бана: access-токен, выданный до бана, живёт до 15 минут; refresh закрыт сразу.
- Denylist `sid` — fail-open при сбое Redis: окно риска не больше access TTL.
- Проекция match может отставать от токена identity; нужен алерт на лаг consumer `social.social.v1`.
- Санкции на `guest_id` не действуют: гость — сессия без аккаунта.
- Иерархии модераторов нет (модератор может санкционировать admin) — решение владельца, пункт в бэклоге.
- Журнал `sanctions` append-only: откат миграции social `0004` прерывается, если в нём есть записи;
  проекции identity/match откатываются свободно и восстанавливаются повтором событий.

## Последствия
Задержка действия через claim — до 15 минут, для онлайна — мгновенно через проекцию. Зона повышенной
безопасности: `security-reviewer`, `review-migration`, ручная проверка владельцем.
