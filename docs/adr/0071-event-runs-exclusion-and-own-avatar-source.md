# 0071. Запуски событий: `EXCLUDE` в БД; адрес своей аватарки — в `me/appearance`

- **Статус:** черновик (уточняет [0054](0054-seasonal-events-model.md), [0048](0048-promo-codes-custom-avatar.md), [0060](0060-postgres-runtime-role.md))
- **Дата:** 2026-10-26

## Контекст
1. Пересечение периодов запусков (ADR 0054) отвергали только advisory-lock и проверка в транзакции:
   прямая запись или баг в обход публикации могли оставить два активных запуска.
2. Адрес своей аватарки отдавался двумя путями: `GET /v1/me/avatar` (`current_url`) и
   `GET /v1/appearance` (`custom_avatar_url` на игрока); `GET /v1/me/appearance` его не имел, и bff
   склеивал блок оформления профиля из двух вызовов (сбой `/me/avatar` гасил оба блока).

## Решение
- Миграция progression `0011`: `EXCLUDE USING gist (tstzrange(starts_at, конец, '[)') WITH &&)
  WHERE status IN ('published','active','finished','stopped')`, конец = `GREATEST(starts_at,
  LEAST(ends_at, COALESCE(stopped_at, ends_at)))` — предикат совпадает с `overlaps()`. Смежные
  периоды `[)` допустимы, остановка освобождает время после неё, черновики не участвуют. Проверка
  в транзакции остаётся (409 `event_period_overlap`), ограничение — последняя линия. Расширение
  `btree_gist` не нужно (только `&&` по диапазону), скрипт БД не меняется. Миграция берёт
  `ACCESS EXCLUSIVE` на крошечную `event_runs` под `lock_timeout 10s`, при существующих
  пересечениях падает с перечнем id до изменения схемы. Откат снимает только ограничение.
- `GET /v1/me/appearance` возвращает `MyAppearance` (слоты + `custom_avatar_url`,
  `custom_avatar_asset_id`) — единый источник для профиля; `MyAvatar.current_url` помечен
  `deprecated` и удаляется через один релиз (потребители вне репозитория через gateway возможны).
  `PlayerAppearance.custom_avatar_url` (чужие игроки, пачка) не меняется. bff читает адрес только из
  `me/appearance`, блоки `appearance` и `avatar` профиля независимы.

## Последствия
- Гонка или прямая вставка пересечения получает `23P01` вместо тихого дубля.
- Контракт `progression.json` расширяется (необязательное для клиента добавление полей ответа).
