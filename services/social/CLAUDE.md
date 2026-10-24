# social

Друзья, комментарии, жалобы, модерация, шкалы поведения и санкции, приватность.

- Пакет: `social_service`; запуск: `social api|relay|consumer|worker|all|migrate`
- Карточка сервиса (данные, события, деградация): `docs/architecture/services.md`
- Публичный API: `/api/social/v1/...` на gateway; admin — `/admin/v1/cases|users|sanctions`
- Зависимости: PostgreSQL, Redis (presence, лимиты), Redpanda
- Статус: этап 5c, шаг 0a — контракты и заглушки 501 (`docs/plans/stage-5.md` §10.1, ADR 0050–0052); домен — потоки F1–F4

## Особенности
- События `social.social.v1` — `contracts/events/social/`; конфиг (лимиты, причины жалоб, пределы санкций) — `contracts/social/social_config.v1.json`, значений в коде нет.
- Схемы API — `api/schemas.py`; ручки друзей — `api/public/friends.py` (F1), комментарии/жалобы/свои санкции — `api/public/discussion.py` (F2, F3), админка — `api/admin/__init__.py` (F3, F4).
- Настройка приватности `friend_requests = all|none` — «кто может звать в друзья»; скрытый профиль, блокировка и неизвестный игрок отвечают одинаково (нет перечисления).
- Текст жалоб и комментариев в события не попадает; жалоба идемпотентна по `(автор, объект)`; порог за сутки открывает кейс ровно один раз.
- Санкции: `UserSanctionsChanged` несёт полное активное состояние и `version`; `ACCOUNT` — флаг `account_banned`, не бит claim. Модератор не санкционирует себя. Права: `social.moderate`, `social.comments.delete_any`, `social.sanctions.manage`, `social.sanctions.account`.
- Шкала порядочности — только счётчики для модератора (из `MatchFinished.players[].finish`, `PlayerRated`), автосанкций нет.
