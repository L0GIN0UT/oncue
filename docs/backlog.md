# Бэклог

Небольшие доработки, не вошедшие в этап. Крупные хвосты этапов — в [roadmap](09-roadmap.md) (разделы «Хвосты»). Запись удаляется, когда пункт сделан.

## Этап 5b

| Пункт | Где | Что сделать |
|---|---|---|
| GET предмета по id в admin-API | progression, admin | `GET /admin/v1/items/{id}`: админка ищет предмет листанием списка (до 20 страниц по 100) |
| GET карточки UGC-изображения по id в admin-API | media_pipeline, admin | `GET /admin/v1/ugc-images/{id}`: сейчас карточка ищется в списке нужного статуса |
| Число получателей в подтверждении рассылки | notifications, admin | Ручка `GET /admin/v1/broadcasts/audience` (число подписчиков с согласием) для страницы подтверждения; сейчас число видно только в списке после постановки в очередь |
| Своя аватарка в `me/appearance` | progression, bff | Поля `custom_avatar_url` нет в `Appearance`; bff в 5b собирает аватар из `/v1/appearance?user_ids=` и `/v1/me/avatar`. Добавление в общую модель дублирует `PlayerAppearance.custom_avatar_url`, поэтому убрать дубль одним изменением контракта |
| Включение и выключение типов уведомлений через admin-API | notifications | Сами типы, шаблоны и маппинг остаются файловым конфигом (решение владельца 2026-10-23) |
| Вынос отправки почты в `libs/` | identity, notifications | `SmtpEmailSender`, `LogEmailSender`, `BackgroundEmailSender` сейчас продублированы |
| Ник в странице профиля игрока | bff, progression, frontend | В `/pages/players/{id}` нет `nickname`: `PlayerBadge` показывает «Игрок». Нужен ник в контракте bff (источник — identity или публичный профиль) |
| Рефакторинг фронтенда перед релизом | frontend | Токены темы, eslint-запрет на литералы цвета и тест контраста (поток S5-front 5b) — задел под рефакторинг: свести компоненты к токенам полностью, проверить светлую тему (этап 7), убрать остатки вне `src/styles` |
| Общий `adminContext` в e2e | e2e | `e2e/collections.spec.ts` дублирует `adminContext`/`ADMIN` из `e2e/admin.ts`: перейти на общий модуль |
| Content-Type у one-click отписки | notifications | `POST /v1/unsubscribe/one-click`: RFC 8058 требует `application/x-www-form-urlencoded`; сейчас тело разбирается при любом типе (безопасности не вредит) |
| Анонимное чтение `p/*` в S3 | media_pipeline | Принятый риск (ключ 128 бит, S3 во внутренней сети); при случае перейти на presigned-ссылки gateway |
| Цвет аватарки: 16-битные и CMYK | media_pipeline | `convert("RGB")` обрезает `I;16` и даёт сдвиг цвета у CMYK без ICC: нормализовать или отказывать |
