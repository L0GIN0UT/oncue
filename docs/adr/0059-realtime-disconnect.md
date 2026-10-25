# 0059. Отзыв соединений Centrifugo при бане аккаунта и кике

- **Статус:** черновик
- **Дата:** 2026-10-25
- **Уточняет:** [0052](0052-sanctions-and-enforcement.md)

## Контекст
ADR 0052 отзывает подписку (`unsubscribe`) при `ONLINE` и кике, но соединение остаётся: забаненный аккаунт держит WebSocket и получает публикации других каналов. Server API key Centrifugo есть только у публикующих сервисов (match, notifications); по D9 identity ключ не получает.

## Решение
- Disconnect делает **match**, не identity: по уже потребляемому `social.UserSanctionsChanged` с `account_banned = true` вызывает `POST {api_url}/disconnect` (`{"user", "disconnect": {"code", "reason"}}`, `X-API-Key`) с кодом 3500 (без переподключения), `reason=account_banned`, и снимает игрока с очереди и из комнаты (`evict`). Дубль и устаревшая версия отсечены проекцией `sanctions_proj`, повтор безопасен. Identity ключ не получает.
- Кик из комнаты: после `unsubscribe` - disconnect с кодом 3000 (клиент переподключится, но subscription-токен на комнату match не выдаст: он не участник).
- Коды: 3000-3499 - клиент переподключается, 3500-3999 - нет (константы в `infrastructure/realtime.py`). Повторы - `GV_MATCH__UNSUBSCRIBE_ATTEMPTS`, метрика сбоев `gv_match_disconnect_failed_total`; сбой не роняет владельца комнаты и обработчик события (лог + метрика).

## Остаточный риск
Centrifugo OSS не умеет отзывать выданные токены. Враждебный клиент может переподключиться старым connection-токеном (TTL `GV_REALTIME__TOKEN_TTL_S`, не более 10 минут) и пересесть на канал старым subscription-токеном. Для честных клиентов disconnect срабатывает сразу. Новые токены забаненному не выдаются (проверка санкции при выдаче/продлении, 0052).

## Вариант без реализации
Connect/subscribe proxy Centrifugo: на каждое подключение/подписку Centrifugo вызывает наш endpoint, который проверяет санкцию и членство. Закрывает риск полностью, но добавляет синхронную зависимость realtime от сервиса на горячем пути - отдельное решение.

## Дополнение (security-ревью): бан-отзыв мимо очереди
Disconnect при `account_banned` не ставится в фоновую очередь `RevocationQueue` ([0063](0063-match-shared-command-reader.md)): задание могло потеряться (переполнение, `aclose`, сброс при SIGTERM), а повтор события невозможен - дубль отсекает проекция. Consumer вызывает адаптер синхронно через `DirectRevoker` (`infrastructure/revocation.py`, повторы - внутри адаптера, `GV_MATCH__UNSUBSCRIBE_ATTEMPTS`). Провал после повторов - лог `match.direct_disconnect_failed` и `gv_match_disconnect_failed_total`, исключение не бросается (иначе откатится проекция санкций). Кик и выход по-прежнему идут через очередь.
