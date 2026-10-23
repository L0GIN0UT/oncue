# notifications

In-app уведомления, realtime-доставка, e-mail, web-push, отключение по типам.

- Пакет: `notifications_service`; запуск: `notifications api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: `/api/notifications/v1/...` на gateway
- Зависимости: PostgreSQL, Redpanda, RabbitMQ
- Статус: этап 5b, шаг 0a — контракты и заглушки 501 (`docs/plans/stage-5.md` §9.5, ADR 0047); домен, consumers и доставка — поток S2.

## Особенности
- Терминальный потребитель: событий не публикует. Маппинг «событие → тип», каналы и шаблоны (ru) — конфиг `contracts/notifications/notification_types.v1.json`, текстов и типов в коде нет; нет шаблона на `default_locale` — конфиг не проходит проверку.
- Дедупликация — по `dedup_key` из полей события (предмет события), не по `event_id`.
- Запись в БД первична, realtime (личный канал Centrifugo) — вторично: недоступный Centrifugo не теряет уведомление.
- E-mail только организационный (рассылка админа — с согласием и отпиской по подписанной ссылке, `POST /v1/unsubscribe` без входа); Push — порт `PushSender` и таблица `devices`, реализации нет.
- Мутации из браузера (`read`, настройки, устройства) — сервис в `COOKIE_MUTATION_SERVICES`. Право рассылки: `notifications.broadcast.send`.
