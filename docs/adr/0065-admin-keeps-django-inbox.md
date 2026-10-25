# 0065. Admin оставляет собственный inbox на Django ORM

- **Статус:** черновик
- **Дата:** 2026-10-25
- **Закрывает:** хвост этапа 1 «общий подключаемый inbox для Django»

## Контекст
`libs/messaging` реализует inbox на SQLAlchemy async (`Inbox.try_mark_processed` в транзакции `EventProcessor`). Admin — Django: синхронный ORM, свои миграции, транзакции `django.db.transaction`. Потребитель admin один (`identity.user.v1`), эффект события — обновление зеркала `AdminUser` под блокировкой строки и проверка `identity_version`, а таблица отметок — `ProcessedEvent`.

## Решение
Оставить собственный inbox admin (`core.events.UserEventsProcessor`, `ProcessedEvent`). Общей остаётся граница: транспорт `KafkaEventConsumer` зависит от протокола `EventProcessorPort`, admin подставляет свою реализацию без `cast`. Выносить в `libs/messaging` порт inbox с двумя адаптерами (async SQLAlchemy и sync Django) не нужно.

## Обоснование
- Дубль логики мал: одна вставка `ON CONFLICT DO NOTHING` в транзакции обработчика; идемпотентность дополнительно держит `identity_version`.
- Адаптер под Django потребовал бы `sync_to_async` внутри общей библиотеки или зависимость libs от Django; оба варианта ухудшают границу «libs без фреймворков сервиса» сильнее, чем один дублированный `INSERT`.
- Второго Django-сервиса нет; если появится — пересмотреть (порт `InboxStore` + адаптеры).

## Последствия
Контракт поведения inbox (ровно один эффект, отметка в транзакции обработчика, версия агрегата) закреплён тестами admin (`test_events`), а не общим кодом. Изменения семантики inbox в `libs/messaging` нужно дублировать в admin вручную — это указано в `services/admin/CLAUDE.md`.
