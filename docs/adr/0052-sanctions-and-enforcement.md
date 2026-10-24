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

## Последствия
Задержка действия через claim — до 15 минут, для онлайна — мгновенно через проекцию. Зона повышенной
безопасности: `security-reviewer`, `review-migration`, ручная проверка владельцем.
