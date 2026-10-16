# 0028. Сборники: версии, черновик с оптимистичной блокировкой, проекция в game, сборник в параметрах старта

- **Статус:** предложено (план этапа 3b, утверждён владельцем)
- **Дата:** 2026-10-16
- **Уточняет:** [0008](0008-plugin-game-engine.md), [движок](../02-game-engine.md) §5

## Контекст
Пользователь собирает сборник из единиц каталога и играет его в game. Сессия должна
доигрываться на том составе, с которым началась, даже если автор правит сборник. Автор
правит черновик с двух устройств. Синхронный вызов game → collections при старте сессии
добавил бы связность на горячем пути, а чужую БД читать нельзя. Конфиг режима на каждый
сборник — тысячи конфигов.

## Решение
- **Модель (D1):** агрегат `collections` (статус `draft/published/hidden/deleted`, `kind
  ugc/curated`, `visibility public/unlisted`), неизменяемые `collection_versions`
  (`content_version`) и один черновик `collection_drafts` с `revision`. Публикация
  черновика создаёт `content_version + 1`. Монотонная `version` агрегата общая для всех
  событий сборника.
- **Конфликт устройств (D2):** `PUT /v1/collections/{id}/draft {base_revision, body,
  client_label}`. Расхождение ревизий — 409 `collections.draft_conflict` с
  `details.current`. Слияния по полям нет, клиент показывает экран выбора.
- **Проекция в game (D3):** game слушает `collections.collection.v1`
  (`CollectionPublished|Updated|Deleted`, полный снимок состава) и ведёт
  `collection_proj`/`collection_items_proj`. Адаптер `CollectionPort.items(collection_id,
  limit, *, playable_for)` пересекает их с активными `catalog_items` и играбельностью.
- **Режим сборника — шаблон (D4):** конфиги `<domain>.collection.<clue>` с source
  `collection` (`round_options ⊆ {5, 10, 25, "all"}`, `min_items`, `shuffle`). Сборник и
  число раундов приходят в `POST /v1/sessions` (`collection_id`, `rounds`) и попадают в
  `DrawContext.collection_id`; `CollectionSource.rounds_for` выбирает число раундов.
- **Спойлеры (D6):** состав видят автор, админ и игрок со статусом «пройден»; поиск — по
  названию, описанию и тегам, не по единицам.
- **«Пройден» и сложность (D9):** по `game.GameFinished` с `collection_id` и
  `collection_version` (новые необязательные поля). Первое завершение пользователя на
  версию, автор не в счёт. Сложность = 1 − байесовское среднее `score/max_score`
  (`GV_COLLECTIONS__DIFFICULTY__*`), до `min_plays` — `null`.
- **Каталог сборников (D10):** Postgres (`tsvector` `russian`+`simple`, GIN, `pg_trgm`,
  keyset-курсор), без OpenSearch.

## Последствия
- Сессия фиксирует `collection_version`; скрытие или правка сборника во время игры её не
  ломают. Скрытый или удалённый сборник не стартует (404 `game.collection_not_found`).
- `CollectionParams` меняется до первой регистрации источника `collection` в game:
  сохранённых конфигов с `collection_id` нет.
- Состав сборника хранится дважды (collections — источник истины, game — проекция);
  согласованность — по `version` событий.
- Переход каталога на OpenSearch — отдельным ADR, когда упрёмся в Postgres.
