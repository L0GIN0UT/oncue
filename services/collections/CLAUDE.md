# collections

Пользовательские и кураторские сборники: конструктор, черновики, каталог, лайки, избранное.

- Пакет: `collections_service`; запуск: `collections api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md` §7
- Публичный API: `/api/collections/v1/...` на gateway; admin — `/admin/v1/collections|authors`
- Зависимости: PostgreSQL (`pg_trgm`), Redis (лимиты), Redpanda; `gv_text.ugc`/`gv_text.emoji`
- Статус: этап 3b, поток K — домен, каталог, реакции, consumers, admin (`docs/plans/stage-3b.md` §5, ADR 0028, 0030)

## Особенности
- Агрегат `domain/collection.py`: `content_version` — версии состава (`collection_versions` неизменяемы, `collection_items` — текущий состав), `version` — общая монотонная для всех событий топика; лайк двигает её атомарным UPDATE (`like_added`), а не через агрегат.
- Ревизия черновика монотонна у агрегата (`draft_seq`): после публикации черновика нет (`revision = 0` в `GET .../draft`), старая ревизия другого устройства не совпадёт (нет ABA). 409 `draft_conflict` несёт `details.current` = `DraftOut`.
- Черновик хранится как прислан (только лимиты `items_max`/`tags_max`); нормализация `gv_text.ugc`, denylist, словари и строгие эмодзи — при публикации (`domain/policy.py`, все ошибки сразу `[{path, code, reason?}]`).
- Use-cases возвращают JSON (форма схем API) — его же хранит `Idempotency-Key` в той же транзакции; API только валидирует модели. Чужое — всегда 404 `collections.not_found`.
- Видимость (D6) — `domain/access.py`: состав — автору, админу (`admin.access`+`collections.view`) и завершившему текущую версию; свои эмодзи — автору и админу; гость = аноним.
- Лимиты: публикация — Redis fail-closed (503 `collections.limiter_unavailable`), лимит тратится после проверок; черновики — счётчиком в БД под advisory lock автора; реакции и поиск — fail-open с метрикой.
- Каталог (D10): `title_tsv` (russian+simple: название — A, описание и слова тегов из `meta` — B) пишет репозиторий; поиск — MATERIALIZED-CTE совпадений (иначе планировщик обходит индекс сортировки целиком); keyset `(значение, id)`, курсор привязан к сортировке.
- Словари тегов и пресетов — `config/meta.yaml` (`GV_COLLECTIONS__META_CONFIG`); битый файл — ошибка старта.
- Consumers: `MediaItemBlocked` убирает единицу первой порцией (`GV_COLLECTIONS__SWEEP__BATCH`), остальное — задача `collections.blocked.sweep`; `GameFinished` — только пользователь, не автор, первое завершение на версию; `UserDeleted` — черновики удаляются, опубликованные обезличиваются (`CollectionUpdated`), лайки/избранное/завершения стираются.
- `social.ContentModerated` (`decision = content_removed`, `target_kind = collection`) → `ModerationEvents.content_removed`: сборник скрывается тем же механизмом, что ручное скрытие (`Collection.hide`, `CollectionUpdated` со `status = hidden`, аудит `collection.hide` с `source = social`); сборника нет/не опубликован/уже скрыт — без изменений. Остальные решения игнорируются.
- `popular_score` пересчитывает `collections.popular.recompute` (окно `GV_COLLECTIONS__POPULAR__*`, дни в `play_stats`); сложность — сразу в consumer, корзина — при чтении по текущим порогам.
- `CollectionPublished/Updated` несут необязательные `title` (≤ 120, нормализованное) и `tags` (ADR 0067); описание в событиях по-прежнему нет.
- Ошибки публикации (422 `collections.publish_invalid`): `details.errors[] = {path, code, message, min?, max?, actual?, reason?}`; `code` — стабильный контракт, `message` — готовый русский текст (`domain/error_messages.py`), заголовок ответа перечисляет первые три причины. Новый код политики без текста ловит `tests/unit/test_error_messages.py`.
- Админ-удаление пользовательского сборника: `DELETE /admin/v1/collections/{id}?reason=` (право `collections.unpublish`, `Idempotency-Key`, аудит `collection.delete`); черновик, опубликованный, скрытый; повтор — 204; кураторский — 409 `collections.not_ugc`.
