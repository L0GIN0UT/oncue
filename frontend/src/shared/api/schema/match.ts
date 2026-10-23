/* eslint-disable */
// Сгенерировано `pnpm gen:api` из contracts/openapi/match.json. Не править руками.

export interface paths {
    "/admin/v1/matches/{match_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Итог матча
         * @description Ошибки: 404 `match.match_not_found`.
         */
        get: operations["get_match_admin_v1_matches__match_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/rooms": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Активные комнаты
         * @description Комнаты в любой фазе, кроме `closed`; фильтр по фазе.
         */
        get: operations["list_rooms_admin_v1_rooms_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/rooms/{code}/abort": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Принудительно завершить комнату
         * @description Команда уходит владельцу комнаты: матч завершается с `finish = aborted` (итог и `MatchFinished {aborted: true}`), комната — `closed`. Ошибки: 404 `match.room_not_found`, 409 `match.room_closed`.
         */
        post: operations["abort_room_admin_v1_rooms__code__abort_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/matches/{match_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Итог матча
         * @description Участникам и всем пользователям (публичный итог без личных данных). Ошибки: 404 `match.match_not_found` (нет или ещё идёт).
         */
        get: operations["get_match_v1_matches__match_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/matches/{match_id}/rate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Похвалить или пожаловаться
         * @description Только участник об участнике того же матча, один раз на пару и вид (`PlayerRated`). Ошибки: 404 `match.match_not_found`, 403 `match.not_participant`, 422 `match.cannot_rate_self`, 409 `match.already_rated`, 429 `match.rate_limited`.
         */
        post: operations["rate_v1_matches__match_id__rate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/me/matches": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Мои матчи
         * @description Сыгранные матчи, новые первыми (keyset-курсор).
         */
        get: operations["my_matches_v1_me_matches_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/meta": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Сведения о сервисе */
        get: operations["meta_v1_meta_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/queue/enter": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Встать в очередь
         * @description Рейтинговая дуэль 1×1 по ladder-у и формату; в очереди ровно одна запись на игрока (повтор с тем же ключом или тем же ladder/format — то же состояние). Рейтинг для подбора — из проекции `rating.RatingChanged` (синхронного вызова rating нет). Допускается любой аккаунт; штрафов по времени нет. Ошибки: 403 `match.ranked_forbidden` (санкция `RANKED`), 404 `match.ladder_not_found`, 422 `match.queue_format_unavailable`, 409 `match.active_room_conflict` (`details.code` — комната, `details.queue` — другая очередь), 409 `idempotency.key_reused`, 429 `match.rate_limited`, 503 `match.game_unavailable`.
         */
        post: operations["queue_enter_v1_queue_enter_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/queue/leave": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Выйти из очереди
         * @description Идемпотентно: вне очереди — тоже 204. Уход из очереди до матча — без штрафа; если подбор уже состоялся, а отсчёт идёт, — выход из комнаты без штрафа (срыв отсчёта, соперник возвращается в очередь); когда матч идёт — ничего не делает (сдача и выход — команды комнаты). Пока пара резервируется и создаётся комната — 409 `match.queue_matching` (повторить через секунду).
         */
        post: operations["queue_leave_v1_queue_leave_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/queue/pool": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Размер очередей
         * @description Сколько игроков ищут матч по каждому ладдеру и формату (для хаба онлайна).
         */
        get: operations["queue_pool_v1_queue_pool_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/queue/tick": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Состояние очереди (heartbeat)
         * @description Раз в `GV_MATCH__TICK_S`: продлевает запись в очереди и отдаёт состояние; при `matched` — код комнаты. Нет записи (истёк heartbeat или игрок вышел) — 404 `match.not_in_queue`; 429 `match.rate_limited`.
         */
        post: operations["queue_tick_v1_queue_tick_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/rooms": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Создать комнату
         * @description Комната по онлайн-шаблону (`session.kind` ∈ duel/party/lobby); создатель — хост в слоте 0. Переопределения — только из `session.lobby.overridable` (D12). Ошибки: 404 `match.mode_not_found` (нет опубликованного онлайн-шаблона), 422 `match.mode_not_online`, 422 `match.setting_not_allowed` (`details.field`), 422 `match.collection_required`, 422 `match.too_many_collections`, 404 `match.collection_not_found`, 403 `match.ranked_forbidden` (рейтинговые комнаты — только из очереди), 409 `match.active_room_conflict` (`details.code` — текущая комната), 429 `match.rate_limited` (`GV_MATCH__LOBBY_CREATE_PER_USER_PER_HOUR`), 503 `match.game_unavailable`.
         */
        post: operations["create_room_v1_rooms_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/rooms/{code}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Комната по коду
         * @description Экран входа по коду или ссылке: состав, настройки, фаза; без данных матча. Ошибки: 404 `match.room_not_found`; 429 `match.rate_limited` (перебор кодов — `GV_MATCH__JOIN_BY_CODE_PER_IP_PER_MIN`, fail-closed).
         */
        get: operations["get_room_v1_rooms__code__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/rooms/{code}/join": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Войти в комнату
         * @description Повторный вход участника (реконнект) — тот же слот. Ошибки: 409 `match.room_full`, 409 `match.active_room_conflict` (`details.code`), 409 `match.match_in_progress` (матч идёт, а игрок не участник), 403 `match.kicked` (кик — возврат только по новому приглашению хоста). 404 `match.room_not_found` (нет комнаты или она закрыта), 403 `match.not_participant` (не в этой комнате), 409 `match.phase_mismatch` (команда не для текущей фазы), 429 `match.rate_limited`, 503 `match.rooms_unavailable`.
         */
        post: operations["join_v1_rooms__code__join_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/rooms/{code}/kick": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Исключить игрока (хост)
         * @description Только в фазе `lobby`. Ошибки: 403 `match.not_host`, 422 `match.cannot_kick_self`, 404 `match.player_not_found`. 404 `match.room_not_found` (нет комнаты или она закрыта), 403 `match.not_participant` (не в этой комнате), 409 `match.phase_mismatch` (команда не для текущей фазы), 429 `match.rate_limited`, 503 `match.rooms_unavailable`.
         */
        post: operations["kick_v1_rooms__code__kick_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/rooms/{code}/leave": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Выйти из комнаты
         * @description Уход хоста передаёт хоста следующему по слоту; во время матча — выбывание (`finish = left`), очки сохраняются. 404 `match.room_not_found` (нет комнаты или она закрыта), 403 `match.not_participant` (не в этой комнате), 409 `match.phase_mismatch` (команда не для текущей фазы), 429 `match.rate_limited`, 503 `match.rooms_unavailable`.
         */
        post: operations["leave_v1_rooms__code__leave_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/rooms/{code}/match/away": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Отошёл / вернулся
         * @description 404 `match.room_not_found` (нет комнаты или она закрыта), 403 `match.not_participant` (не в этой комнате), 409 `match.phase_mismatch` (команда не для текущей фазы), 429 `match.rate_limited`, 503 `match.rooms_unavailable`.
         */
        post: operations["away_v1_rooms__code__match_away_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/rooms/{code}/match/chat": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Свободный чат (кастомное лобби)
         * @description Только при `chat.free_text` (кастомное лобби, D14): нормализация и denylist `gv_text.ugc` (ADR 0030), ≤ 200 символов, без ссылок. Ошибки: 422 `match.chat_rejected` (`details.reason`: `denylist` | `link` | `too_long`), 403 `match.chat_disabled` (в режиме нет свободного чата), 403 `match.chat_muted` (`CHAT_MUTE`), 429 `match.rate_limited` (`CHAT_SLOW`, `GV_MATCH__CHAT_PER_PLAYER_PER_MIN`). 404 `match.room_not_found` (нет комнаты или она закрыта), 403 `match.not_participant` (не в этой комнате), 409 `match.phase_mismatch` (команда не для текущей фазы), 429 `match.rate_limited`, 503 `match.rooms_unavailable`.
         */
        post: operations["chat_v1_rooms__code__match_chat_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/rooms/{code}/match/emote": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Эмоция или готовая фраза
         * @description Ключ — из `chat.emotes` / `chat.phrases` режима. Ошибки: 422 `match.emote_unknown`, 403 `match.chat_muted` (санкция `CHAT_MUTE`). 404 `match.room_not_found` (нет комнаты или она закрыта), 403 `match.not_participant` (не в этой комнате), 409 `match.phase_mismatch` (команда не для текущей фазы), 429 `match.rate_limited`, 503 `match.rooms_unavailable`.
         */
        post: operations["emote_v1_rooms__code__match_emote_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/rooms/{code}/match/forfeit": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Сдаться
         * @description Дуэль — поражение (`finish = forfeit`), соперник — победа; party — выбывание. 404 `match.room_not_found` (нет комнаты или она закрыта), 403 `match.not_participant` (не в этой комнате), 409 `match.phase_mismatch` (команда не для текущей фазы), 429 `match.rate_limited`, 503 `match.rooms_unavailable`.
         */
        post: operations["forfeit_v1_rooms__code__match_forfeit_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/rooms/{code}/match/guess": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Ответ
         * @description Исход своего ответа и оставшиеся попытки; соперники видят только статус. Ошибки: 409 `match.phase_mismatch` (уровень закрыт, другая `expected_version`), 409 `match.level_locked` (попытки уровня кончились), 422 `match.answer_kind_unsupported`, 422 `match.option_unknown`. 404 `match.room_not_found` (нет комнаты или она закрыта), 403 `match.not_participant` (не в этой комнате), 409 `match.phase_mismatch` (команда не для текущей фазы), 429 `match.rate_limited`, 503 `match.rooms_unavailable`.
         */
        post: operations["guess_v1_rooms__code__match_guess_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/rooms/{code}/match/skip": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Пропустить уровень
         * @description 404 `match.room_not_found` (нет комнаты или она закрыта), 403 `match.not_participant` (не в этой комнате), 409 `match.phase_mismatch` (команда не для текущей фазы), 429 `match.rate_limited`, 503 `match.rooms_unavailable`.
         */
        post: operations["skip_v1_rooms__code__match_skip_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/rooms/{code}/ready": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Готовность в лобби
         * @description 404 `match.room_not_found` (нет комнаты или она закрыта), 403 `match.not_participant` (не в этой комнате), 409 `match.phase_mismatch` (команда не для текущей фазы), 429 `match.rate_limited`, 503 `match.rooms_unavailable`.
         */
        post: operations["ready_v1_rooms__code__ready_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/rooms/{code}/realtime-token": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Токен подписки на канал комнаты
         * @description Subscription-JWT Centrifugo для `room:{code}` — только участнику (D9; отдельный секрет подписок, публикации клиентов запрещены). Connection-токен — `POST /api/identity/v1/realtime/token`. Наблюдатель (право `match.spectate`) получает токен идущего матча как читатель. Ошибки: 404 `match.room_not_found`, 403 `match.not_participant`, 429 `match.rate_limited`.
         */
        post: operations["realtime_token_v1_rooms__code__realtime_token_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/rooms/{code}/rematch": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Реванш
         * @description Только в фазе `summary`: голос за реванш; когда согласны все оставшиеся — новая комната с теми же настройками (`rematch_code`). 404 `match.room_not_found` (нет комнаты или она закрыта), 403 `match.not_participant` (не в этой комнате), 409 `match.phase_mismatch` (команда не для текущей фазы), 429 `match.rate_limited`, 503 `match.rooms_unavailable`.
         */
        post: operations["rematch_v1_rooms__code__rematch_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/rooms/{code}/reset-memory": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Сбросить память сыгранного (хост)
         * @description Очищает `exclude_ids` лобби: единицы прошлых матчей снова могут выпасть. Ошибки: 403 `match.not_host`. 404 `match.room_not_found` (нет комнаты или она закрыта), 403 `match.not_participant` (не в этой комнате), 409 `match.phase_mismatch` (команда не для текущей фазы), 429 `match.rate_limited`, 503 `match.rooms_unavailable`.
         */
        post: operations["reset_memory_v1_rooms__code__reset_memory_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/rooms/{code}/settings": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Настройки лобби (хост)
         * @description Только в фазе `lobby`; сбрасывает готовность всех. Ошибки: 403 `match.not_host`, 422 `match.setting_not_allowed` (`details.field`), 422 `match.too_many_collections`, 404 `match.collection_not_found`, 409 `match.players_over_limit` (новый `players_max` меньше числа игроков). 404 `match.room_not_found` (нет комнаты или она закрыта), 403 `match.not_participant` (не в этой комнате), 409 `match.phase_mismatch` (команда не для текущей фазы), 429 `match.rate_limited`, 503 `match.rooms_unavailable`.
         */
        post: operations["change_settings_v1_rooms__code__settings_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/rooms/{code}/start": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Начать матч (хост)
         * @description План раундов — от game (`POST /internal/v1/round-plans`, D5), затем отсчёт `countdown_s`. Ошибки: 403 `match.not_host`, 409 `match.not_all_ready`, 409 `match.not_enough_players`, 422 `match.pool_too_small`, 503 `match.game_unavailable` (онлайн временно недоступен). 404 `match.room_not_found` (нет комнаты или она закрыта), 403 `match.not_participant` (не в этой комнате), 409 `match.phase_mismatch` (команда не для текущей фазы), 429 `match.rate_limited`, 503 `match.rooms_unavailable`.
         */
        post: operations["start_v1_rooms__code__start_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/rooms/{code}/state": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Снимок комнаты
         * @description Ресинк после пропуска версии в канале. Снимок фильтруется по зрителю: личное — только в `me`; до `reveal` нет единицы раунда и ключей ответа. Наблюдатель (`match.spectate`) видит идущий матч без `me`. Ошибки: 404 `match.room_not_found`, 403 `match.not_participant`.
         */
        get: operations["room_state_v1_rooms__code__state_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/rooms/{code}/tick": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Heartbeat и HTTP-фолбэк
         * @description Раз в `GV_MATCH__TICK_S` (без WebSocket — 1 с): отметка присутствия (D10) и снимок, если версия новее `known_version`. Ошибки: 404 `match.room_not_found`, 403 `match.not_participant`, 429 `match.rate_limited`.
         */
        post: operations["tick_v1_rooms__code__tick_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/rooms/{code}/transfer-host": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Передать хоста
         * @description Ошибки: 403 `match.not_host`, 404 `match.player_not_found`. 404 `match.room_not_found` (нет комнаты или она закрыта), 403 `match.not_participant` (не в этой комнате), 409 `match.phase_mismatch` (команда не для текущей фазы), 429 `match.rate_limited`, 503 `match.rooms_unavailable`.
         */
        post: operations["transfer_host_v1_rooms__code__transfer_host_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/rooms/open": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Открытые лобби
         * @description Публичные комнаты в фазе `lobby` со свободными местами, новые первыми.
         */
        get: operations["open_rooms_v1_rooms_open_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/rooms/spectate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Комнаты для наблюдения
         * @description Идущие матчи без задержки; только с правом `match.spectate` (модератор, админ) — иначе 403 `auth.forbidden` (D19). Только чтение: подписка на канал — `POST /rooms/{code}/realtime-token`, снимок — `GET /rooms/{code}/state` (оба — с тем же правом), вид без личных данных игроков (`me` пуст, ответов до ревила нет). Лобби и завершённые матчи не показываются. Ошибки: 422 `match.cursor_invalid`, 429 `match.rate_limited` (`GV_MATCH__SPECTATE_PER_USER_PER_MIN`).
         */
        get: operations["spectate_rooms_v1_rooms_spectate_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /**
         * AbortRoomRequest
         * @description Принудительное завершение комнаты: матч — ``finish = aborted``, причина в аудит.
         */
        AbortRoomRequest: {
            /** Reason */
            reason: string;
        };
        /**
         * AdminRoomCard
         * @description Активная комната для раздела «Матчи» админки.
         */
        AdminRoomCard: {
            /** Code */
            code: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Host Id
             * Format: uuid
             */
            host_id: string;
            /** Match Id */
            match_id?: string | null;
            mode: components["schemas"]["ModeRef"];
            /**
             * Owner Epoch
             * @description Эпоха lease владельца (D7); null — комната без владельца
             */
            owner_epoch?: number | null;
            /**
             * Phase
             * @enum {string}
             */
            phase: "lobby" | "countdown" | "level_open" | "level_closed" | "reveal" | "summary" | "paused" | "closed";
            /** Players */
            players: number;
            /**
             * Visibility
             * @enum {string}
             */
            visibility: "public" | "private";
        };
        /**
         * AdminRoomsPage
         * @description Страница активных комнат (keyset-курсор).
         */
        AdminRoomsPage: {
            /** Items */
            items: components["schemas"]["AdminRoomCard"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /**
         * AwayRequest
         * @description Отметка «отошёл» / «вернулся».
         */
        AwayRequest: {
            /** Away */
            away: boolean;
            /** Op Id */
            op_id: string;
        };
        /**
         * ChatOptions
         * @description Эмоции, готовые фразы и свободный чат комнаты (D14).
         */
        ChatOptions: {
            /** Emotes */
            emotes: string[];
            /**
             * Free Text
             * @description Свободный чат — только в кастомном лобби
             */
            free_text: boolean;
            /** Phrases */
            phrases: string[];
        };
        /**
         * ChatRequest
         * @description Свободный чат (только кастомное лобби): ≤ 200 символов, без ссылок, denylist 3b.
         */
        ChatRequest: {
            /** Op Id */
            op_id: string;
            /** Text */
            text: string;
        };
        /**
         * ClueMeta
         * @description Параметры уровня загадки для UI (``hint_fields`` плагина ядра).
         */
        ClueMeta: {
            /** Blur Px */
            blur_px?: number | null;
            /** Count */
            count?: number | null;
            /** Crop */
            crop?: number | null;
            /** Fields */
            fields?: string[] | null;
            /** Frame */
            frame?: number | null;
            /** Pixel Block */
            pixel_block?: number | null;
            /** Seconds */
            seconds?: number | null;
        };
        /**
         * CommandAccepted
         * @description 202: команда в потоке комнаты, владелец не ответил вовремя (D6).
         */
        CommandAccepted: {
            /**
             * Accepted
             * @default true
             * @constant
             */
            accepted: true;
            /**
             * Version
             * @description Последняя известная `version` комнаты
             */
            version: number;
        };
        /**
         * CreateRoomRequest
         * @description Создание комнаты по онлайн-шаблону (``session.kind`` ∈ duel/party/lobby).
         */
        CreateRoomRequest: {
            /**
             * Collection Ids
             * @description Сборники для шаблона с source `collection` (≤ `max_collections` режима)
             */
            collection_ids?: string[] | null;
            /** Mode Key */
            mode_key: string;
            settings?: components["schemas"]["SettingsOverride"] | null;
        };
        /**
         * EmoteRequest
         * @description Эмоция или готовая фраза из ``chat.emotes`` / ``chat.phrases`` режима.
         */
        EmoteRequest: {
            /** Key */
            key: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "emote" | "phrase";
            /** Op Id */
            op_id: string;
        };
        /**
         * ForfeitRequest
         * @description Сдаться (дуэль — поражение «Соперник сдался», party — выбыть).
         */
        ForfeitRequest: {
            /** Op Id */
            op_id: string;
        };
        /**
         * GuessRequest
         * @description Ответ на открытом уровне раунда.
         */
        GuessRequest: {
            /** Answer */
            answer: components["schemas"]["ItemAnswer"] | components["schemas"]["TextAnswer"] | components["schemas"]["OptionAnswer"];
            /**
             * Expected Version
             * @description `version` комнаты, которую видит клиент
             */
            expected_version: number;
            /** Level */
            level: number;
            /** Op Id */
            op_id: string;
            /** Round Index */
            round_index: number;
        };
        /**
         * GuessResult
         * @description Итог своего ответа (время и скорость считает только сервер).
         */
        GuessResult: {
            /** Attempts Left */
            attempts_left: number;
            /**
             * Level Closed
             * @description Уровень закрыт для меня (угадал или попытки кончились)
             */
            level_closed: boolean;
            me: components["schemas"]["MyRoundView"];
            /**
             * Result
             * @description null у пропуска
             */
            result: ("exact" | "partial" | "miss") | null;
            /** Version */
            version: number;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /**
         * ItemAnswer
         * @description Ответ выбором из автокомплита каталога.
         */
        ItemAnswer: {
            /**
             * Media Item Id
             * Format: uuid
             */
            media_item_id: string;
        };
        /**
         * JoinRequest
         * @description Войти в комнату по коду.
         */
        JoinRequest: {
            /** Op Id */
            op_id: string;
        };
        /**
         * LeaveRequest
         * @description Выйти из комнаты (во время матча — выбыть, очки сохраняются).
         */
        LeaveRequest: {
            /** Op Id */
            op_id: string;
        };
        /**
         * LevelClue
         * @description Ссылка на ассет открытого уровня (одна на комнату, раунд и уровень, D11).
         */
        LevelClue: {
            /**
             * Expires At
             * Format: date-time
             */
            expires_at: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "audio" | "image" | "text";
            /** Level */
            level: number;
            meta: components["schemas"]["ClueMeta"];
            /** Mime */
            mime: string;
            /** Url */
            url: string;
        };
        /**
         * MatchCommandResult
         * @description Ответ команд матча без личного итога (skip, forfeit, emote, chat, away).
         */
        MatchCommandResult: {
            /** Version */
            version: number;
        };
        /**
         * MatchPlayerView
         * @description Игрок в матче для всех зрителей: статус без текста ответа.
         */
        MatchPlayerView: {
            /**
             * Round Points
             * @description Очки за раунд — с фазы reveal
             */
            round_points?: number | null;
            /** Score */
            score: number;
            /**
             * Status
             * @description null вне фазы уровня
             */
            status: ("thinking" | "answered" | "missed" | "guessed") | null;
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
        };
        /**
         * MatchResult
         * @description Итог матча (``GET /v1/matches/{id}``).
         */
        MatchResult: {
            /** Collection Ids */
            collection_ids?: string[];
            /**
             * Finish
             * @enum {string}
             */
            finish: "completed" | "forfeit" | "tech_win" | "aborted";
            /**
             * Finished At
             * Format: date-time
             */
            finished_at: string;
            /**
             * Match Id
             * Format: uuid
             */
            match_id: string;
            mode: components["schemas"]["ModeRef"];
            /** Players */
            players: components["schemas"]["ResultPlayer"][];
            /**
             * Room Code
             * @description Только участникам матча (не участнику — null)
             */
            room_code: string | null;
            /** Rounds */
            rounds: components["schemas"]["ResultRound"][];
            /**
             * Started At
             * Format: date-time
             */
            started_at: string;
        };
        /**
         * MatchView
         * @description Матч для любого участника (и в дельтах канала): без плана и ключей ответа.
         */
        MatchView: {
            /** @description Только после открытия уровня (`level_opened`) */
            clue?: components["schemas"]["LevelClue"] | null;
            /**
             * Level
             * @description Открытый уровень
             */
            level?: number | null;
            /** Levels Count */
            levels_count: number;
            /**
             * Match Id
             * Format: uuid
             */
            match_id: string;
            /** Players */
            players: components["schemas"]["MatchPlayerView"][];
            /** @description Только в фазе reveal */
            reveal?: components["schemas"]["RoundReveal"] | null;
            /** Round Index */
            round_index: number;
            /** Rounds Total */
            rounds_total: number;
        };
        /**
         * ModeRef
         * @description Шаблон режима комнаты (версия фиксируется при создании).
         */
        ModeRef: {
            /** Answer Format */
            answer_format: string;
            /** Clue Type */
            clue_type: string;
            /**
             * Domain
             * @enum {string}
             */
            domain: "music" | "film";
            /** Key */
            key: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "duel" | "party" | "lobby";
            /**
             * Ranked
             * @default false
             */
            ranked: boolean;
            /** Title Key */
            title_key: string;
            /** Version */
            version: number;
        };
        /**
         * MyMatchCard
         * @description Матч в «Моих матчах».
         */
        MyMatchCard: {
            /**
             * Finish
             * @enum {string}
             */
            finish: "completed" | "forfeit" | "tech_win" | "aborted";
            /**
             * Finished At
             * Format: date-time
             */
            finished_at: string;
            /**
             * Match Id
             * Format: uuid
             */
            match_id: string;
            mode: components["schemas"]["ModeRef"];
            /**
             * My Finish
             * @enum {string}
             */
            my_finish: "completed" | "forfeit" | "left" | "tech_win" | "tech_loss";
            /** Outcome */
            outcome: ("win" | "loss" | "draw") | null;
            /** Place */
            place: number;
            /** Players */
            players: number;
            /** Score */
            score: number;
        };
        /**
         * MyMatchesPage
         * @description Страница моих матчей (keyset-курсор по ``finished_at`` убыв.).
         */
        MyMatchesPage: {
            /** Items */
            items: components["schemas"]["MyMatchCard"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /**
         * MyRoundView
         * @description Личное состояние раунда: только в ответе команды и в ``GET /state`` (D9).
         */
        MyRoundView: {
            /** Attempts Left */
            attempts_left: number;
            /** Guesses */
            guesses: components["schemas"]["OwnGuess"][];
            /** Options */
            options?: components["schemas"]["OptionOut"][];
            /** Round Index */
            round_index: number;
            /** Status */
            status: ("thinking" | "answered" | "missed" | "guessed") | null;
        };
        /**
         * OpenRoomCard
         * @description Открытое лобби в списке (``visibility = public``, фаза ``lobby``).
         */
        OpenRoomCard: {
            /** Code */
            code: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Host Nickname */
            host_nickname: string;
            mode: components["schemas"]["ModeRef"];
            /** Players */
            players: number;
            /** Players Max */
            players_max: number;
            settings: components["schemas"]["RoomSettings"];
        };
        /**
         * OpenRoomsPage
         * @description Страница открытых лобби (keyset-курсор).
         */
        OpenRoomsPage: {
            /** Items */
            items: components["schemas"]["OpenRoomCard"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /**
         * OptionAnswer
         * @description Ответ вариантом (формат ``pick``).
         */
        OptionAnswer: {
            /** Option Id */
            option_id: string;
        };
        /**
         * OptionOut
         * @description Вариант ответа формата ``pick``: ``option_id`` непрозрачен и перемешан на комнату.
         */
        OptionOut: {
            /** Label Key */
            label_key?: string | null;
            /** Option Id */
            option_id: string;
            /** Title */
            title?: string | null;
        };
        /**
         * OwnGuess
         * @description Своя попытка (видна только себе).
         */
        OwnGuess: {
            /** Display */
            display: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "item" | "text" | "option" | "skip";
            /** Level */
            level: number;
            /**
             * Result
             * @description null у пропуска
             */
            result: ("exact" | "partial" | "miss") | null;
        };
        /**
         * PersonOut
         * @description Персона в ревиле.
         */
        PersonOut: {
            /** Name */
            name: string;
            /** Role */
            role: string;
        };
        /**
         * PlayerSlot
         * @description Место игрока в комнате.
         */
        PlayerSlot: {
            /**
             * Away
             * @description Игрок отметил «отошёл» (`match/away`)
             * @default false
             */
            away: boolean;
            /** Is Host */
            is_host: boolean;
            /** Nickname */
            nickname: string;
            /**
             * Presence
             * @enum {string}
             */
            presence: "online" | "disconnected" | "left";
            /**
             * Ready
             * @description Готовность в лобби; сбрасывается при смене настроек
             */
            ready: boolean;
            /**
             * Score
             * @description Очки в текущем матче
             * @default 0
             */
            score: number;
            /** Slot */
            slot: number;
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
        };
        /**
         * Problem
         * @description RFC 9457 Problem Details — тело любого ответа-ошибки сервиса (для OpenAPI).
         *
         *     Attributes:
         *         type: URN проблемы ``urn:guessverse:problem:<code>``.
         *         title: Человекочитаемый заголовок.
         *         status: HTTP-статус.
         *         code: Машинный код ошибки — на него опираются клиенты.
         *         detail: Пояснение.
         *         correlation_id: Идентификатор цепочки запроса (для поиска в логах).
         *         details: Безопасные для клиента подробности (``AppError.details``).
         *         errors: Ошибки валидации полей запроса.
         */
        Problem: {
            /** Code */
            code: string;
            /** Correlation Id */
            correlation_id?: string | null;
            /** Detail */
            detail?: string | null;
            /** Details */
            details?: {
                [key: string]: unknown;
            } | null;
            /**
             * Errors
             * @description Ошибки валидации: loc, msg, type
             */
            errors?: {
                [key: string]: unknown;
            }[] | null;
            /** Status */
            status: number;
            /** Title */
            title: string;
            /** Type */
            type: string;
        };
        /**
         * QueueEnterRequest
         * @description Встать в очередь рейтинговой дуэли (ADR 0038).
         *
         *     Рейтинг общий на ladder (домен), очередь — отдельная на формат: игрок состоит ровно в
         *     одной очереди. Шаблон режима выбирает match по ``ladder`` и ``format`` (рейтинговая
         *     дуэль домена с ``answer.format`` = ``classic`` или ``pick``).
         */
        QueueEnterRequest: {
            /**
             * Format
             * @enum {string}
             */
            format: "classic" | "lite";
            /** Ladder */
            ladder: string;
        };
        /**
         * QueuePool
         * @description Размер очередей (хаб онлайна); без списка игроков.
         */
        QueuePool: {
            /** Items */
            items: components["schemas"]["QueuePoolEntry"][];
        };
        /**
         * QueuePoolEntry
         * @description Сколько игроков ищут матч в очереди ladder-а и формата.
         */
        QueuePoolEntry: {
            /**
             * Format
             * @enum {string}
             */
            format: "classic" | "lite";
            /** Ladder */
            ladder: string;
            /** Searching */
            searching: number;
        };
        /**
         * QueueTicket
         * @description Состояние игрока в очереди (ответ ``enter`` и ``tick``).
         */
        QueueTicket: {
            /**
             * Entered At
             * Format: date-time
             */
            entered_at: string;
            /**
             * Format
             * @enum {string}
             */
            format: "classic" | "lite";
            /** Ladder */
            ladder: string;
            /**
             * Room Code
             * @description Комната дуэли, когда `state = matched` (сразу в `countdown`)
             */
            room_code?: string | null;
            /**
             * Slow
             * @description Ждёт дольше `slow_hint_after_s`: показать «ищем дольше обычного»
             */
            slow: boolean;
            /**
             * State
             * @enum {string}
             */
            state: "searching" | "matched";
            /**
             * Tick S
             * @description Период heartbeat `POST /queue/tick`, с (`GV_MATCH__TICK_S`)
             */
            tick_s?: number | null;
            /**
             * Wait S
             * @description Сколько секунд игрок ждёт
             */
            wait_s: number;
            /**
             * Window
             * @description Текущее окно подбора ±, очков (из конфига ladder-а)
             */
            window: number;
        };
        /**
         * RateRequest
         * @description Похвала или жалоба на игрока после матча (``PlayerRated``, обработка — этап 5).
         */
        RateRequest: {
            /**
             * Kind
             * @enum {string}
             */
            kind: "praise" | "report";
            /**
             * To User Id
             * Format: uuid
             */
            to_user_id: string;
        };
        /**
         * ReadyRequest
         * @description Отметить готовность в лобби.
         */
        ReadyRequest: {
            /** Op Id */
            op_id: string;
            /** Ready */
            ready: boolean;
        };
        /**
         * RealtimeSubscription
         * @description Subscription-токен Centrifugo для канала комнаты (D9, отдельный секрет подписок).
         */
        RealtimeSubscription: {
            /** Channel */
            channel: string;
            /**
             * Expires At
             * Format: date-time
             */
            expires_at: string;
            /** Token */
            token: string;
        };
        /**
         * RematchRequest
         * @description Реванш: новая комната с теми же игроками по согласию (D19).
         */
        RematchRequest: {
            /** Op Id */
            op_id: string;
        };
        /**
         * RematchResult
         * @description Реванш: код новой комнаты (``null`` — ждём согласия остальных).
         */
        RematchResult: {
            /** Rematch Code */
            rematch_code?: string | null;
            room: components["schemas"]["RoomView"];
            /** Version */
            version: number;
        };
        /**
         * ResetMemoryRequest
         * @description Сбросить память сыгранных единиц лобби (только хост).
         */
        ResetMemoryRequest: {
            /** Op Id */
            op_id: string;
        };
        /**
         * ResultPlayer
         * @description Игрок в итоге матча (§2.2: ``place``, ``outcome``, ``finish``).
         */
        ResultPlayer: {
            /**
             * Finish
             * @enum {string}
             */
            finish: "completed" | "forfeit" | "left" | "tech_win" | "tech_loss";
            /** Nickname */
            nickname: string;
            /**
             * Outcome
             * @description Только у дуэли
             */
            outcome: ("win" | "loss" | "draw") | null;
            /**
             * Place
             * @description Место с ничьими: 1, 1, 3
             */
            place: number;
            /** Score */
            score: number;
            /** Slot */
            slot: number;
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
        };
        /**
         * ResultRound
         * @description Раунд в итоге: единица раскрывается после матча (карточку отдаёт bff из catalog).
         */
        ResultRound: {
            /** Index */
            index: number;
            /**
             * Media Item Id
             * Format: uuid
             */
            media_item_id: string;
            /** Results */
            results: components["schemas"]["RoundPlayerResult"][];
            /**
             * Void
             * @description Раунд аннулирован (ассет недоступен)
             * @default false
             */
            void: boolean;
        };
        /**
         * RoomCommandResult
         * @description Ответ команды комнаты: новое состояние комнаты.
         */
        RoomCommandResult: {
            room: components["schemas"]["RoomView"];
            /** Version */
            version: number;
        };
        /**
         * RoomSettings
         * @description Действующие настройки комнаты (значения шаблона с переопределениями хоста).
         */
        RoomSettings: {
            /** Answer Format */
            answer_format: string;
            /** Clue Type */
            clue_type: string;
            /** Collection Ids */
            collection_ids?: string[];
            /**
             * Level Window S
             * @description Окно уровня, с (у шаблона — первое окно)
             */
            level_window_s: number;
            /** Players Max */
            players_max: number;
            /** Rounds */
            rounds: number;
            /**
             * Visibility
             * @enum {string}
             */
            visibility: "public" | "private";
        };
        /**
         * RoomState
         * @description Полный снимок для зрителя-участника: ресинк после пропуска версии.
         */
        RoomState: {
            match?: components["schemas"]["MatchView"] | null;
            /** @description Только своё; null вне матча */
            me?: components["schemas"]["MyRoundView"] | null;
            room: components["schemas"]["RoomView"];
        };
        /**
         * RoomView
         * @description Комната для всех участников (то же, что в дельтах канала ``room:{code}``).
         *
         *     ``phase_ends_at`` и ``server_now`` — для таймера клиента с поправкой часов.
         */
        RoomView: {
            chat: components["schemas"]["ChatOptions"];
            /** Code */
            code: string;
            /**
             * Host Id
             * Format: uuid
             */
            host_id: string;
            /**
             * Match Id
             * @description Текущий или последний матч
             */
            match_id?: string | null;
            /**
             * Memory Size
             * @description Сколько единиц в памяти сыгранного лобби (`exclude_ids`)
             * @default 0
             */
            memory_size: number;
            mode: components["schemas"]["ModeRef"];
            /** @description Только у кастомного лобби (`mode.kind = lobby`) */
            options?: components["schemas"]["SettingsOptions"] | null;
            /**
             * Phase
             * @enum {string}
             */
            phase: "lobby" | "countdown" | "level_open" | "level_closed" | "reveal" | "summary" | "paused" | "closed";
            /**
             * Phase Ends At
             * @description Когда сервер закроет фазу; null — без таймера (лобби)
             */
            phase_ends_at?: string | null;
            /** Players */
            players: components["schemas"]["PlayerSlot"][];
            /** Players Min */
            players_min: number;
            /**
             * Rematch Code
             * @description Код комнаты реванша, когда согласились все (у кастомного лобби — свой код: комната возвращается в лобби); старая комната дуэли и party закрывается
             */
            rematch_code?: string | null;
            /**
             * Server Now
             * Format: date-time
             */
            server_now: string;
            settings: components["schemas"]["RoomSettings"];
            /** @description Почему не начался матч (старт мог ответить 202 до отказа game); сбрасывается при следующем старте или смене настроек */
            start_error?: components["schemas"]["StartError"] | null;
            /** Version */
            version: number;
            /**
             * Visibility
             * @enum {string}
             */
            visibility: "public" | "private";
        };
        /**
         * RoundPlayerResult
         * @description Результат игрока в раунде.
         */
        RoundPlayerResult: {
            /**
             * Elapsed Ms
             * @description От открытия уровня (сервер)
             */
            elapsed_ms?: number | null;
            /**
             * Level
             * @description Уровень угадывания; null — не угадал
             */
            level: number | null;
            /** Partial */
            partial: boolean;
            /** Score */
            score: number;
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
        };
        /**
         * RoundReveal
         * @description Ответ раунда — только в фазе ``reveal`` и позже.
         */
        RoundReveal: {
            /** @description null — ассет ревила недоступен */
            final_clue: components["schemas"]["LevelClue"] | null;
            /**
             * Media Item Id
             * Format: uuid
             */
            media_item_id: string;
            /** Persons */
            persons: components["schemas"]["PersonOut"][];
            /** Title */
            title: string;
            /** Year */
            year: number | null;
        };
        /**
         * ServiceMeta
         * @description Сведения о сервисе.
         */
        ServiceMeta: {
            /** Service */
            service: string;
            /** Status */
            status: string;
        };
        /**
         * SettingsOptions
         * @description Что хост может выбрать (``session.lobby.overridable`` шаблона, D12); ``null`` — нельзя.
         *
         *     ``answer_format`` и ``clue_type`` в 4a всегда ``null``: план game их пока не принимает
         *     (выбор — 422 ``match.setting_not_allowed``, ``details.reason = not_supported_yet``).
         */
        SettingsOptions: {
            /** Answer Format */
            answer_format?: string[] | null;
            /** Clue Type */
            clue_type?: string[] | null;
            /** Level Window S */
            level_window_s?: number[] | null;
            /** Max Collections */
            max_collections?: number | null;
            /** Players Max */
            players_max?: number[] | null;
            /** Rounds */
            rounds?: number[] | null;
            /** Visibility */
            visibility?: ("public" | "private")[] | null;
        };
        /**
         * SettingsOverride
         * @description Переопределения хоста; каждое значение — из ``SettingsOptions`` шаблона (D12).
         */
        SettingsOverride: {
            /** Answer Format */
            answer_format?: string | null;
            /** Clue Type */
            clue_type?: string | null;
            /** Level Window S */
            level_window_s?: number | null;
            /** Players Max */
            players_max?: number | null;
            /** Rounds */
            rounds?: number | null;
            /** Visibility */
            visibility?: ("public" | "private") | null;
        };
        /**
         * SettingsRequest
         * @description Сменить настройки (только хост, только в лобби); готовность всех сбрасывается.
         */
        SettingsRequest: {
            /** Collection Ids */
            collection_ids?: string[] | null;
            /** Op Id */
            op_id: string;
            settings?: components["schemas"]["SettingsOverride"] | null;
        };
        /**
         * SkipRequest
         * @description Пропустить открытый уровень (ждать следующего).
         */
        SkipRequest: {
            /**
             * Expected Version
             * @description `version` комнаты, которую видит клиент
             */
            expected_version: number;
            /** Level */
            level: number;
            /** Op Id */
            op_id: string;
            /** Round Index */
            round_index: number;
        };
        /**
         * SpectateRoomCard
         * @description Идущая комната в списке наблюдения (``match.spectate``).
         */
        SpectateRoomCard: {
            /** Code */
            code: string;
            mode: components["schemas"]["ModeRef"];
            /**
             * Phase
             * @enum {string}
             */
            phase: "lobby" | "countdown" | "level_open" | "level_closed" | "reveal" | "summary" | "paused" | "closed";
            /** Players */
            players: number;
            /**
             * Started At
             * Format: date-time
             */
            started_at: string;
        };
        /**
         * SpectateRoomsPage
         * @description Страница комнат для наблюдения (keyset-курсор).
         */
        SpectateRoomsPage: {
            /** Items */
            items: components["schemas"]["SpectateRoomCard"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /**
         * StartError
         * @description Последний отказ старта матча (план game не получен) — виден всем в лобби.
         */
        StartError: {
            /**
             * At
             * Format: date-time
             */
            at: string;
            /**
             * Code
             * @description `match.game_unavailable`, `match.pool_too_small` и т. п.
             */
            code: string;
        };
        /**
         * StartRequest
         * @description Начать матч (только хост, все готовы, игроков ≥ ``players_min``).
         */
        StartRequest: {
            /**
             * Expected Version
             * @description `version` комнаты, которую видит клиент
             */
            expected_version: number;
            /** Op Id */
            op_id: string;
        };
        /**
         * TargetPlayerRequest
         * @description Команда хоста над другим игроком (кик, передача хоста).
         */
        TargetPlayerRequest: {
            /** Op Id */
            op_id: string;
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
        };
        /**
         * TextAnswer
         * @description Ответ свободным текстом.
         */
        TextAnswer: {
            /** Text */
            text: string;
        };
        /**
         * TickRequest
         * @description Heartbeat присутствия и HTTP-фолбэк снимка (D10).
         */
        TickRequest: {
            /**
             * Known Version
             * @description Версия у клиента; снимок вернётся, если есть новее
             */
            known_version?: number | null;
        };
        /**
         * TickResult
         * @description Ответ tick: ``state`` есть, только если версия комнаты новее ``known_version``.
         */
        TickResult: {
            /**
             * Server Now
             * Format: date-time
             */
            server_now: string;
            state?: components["schemas"]["RoomState"] | null;
            /** Version */
            version: number;
        };
        /** ValidationError */
        ValidationError: {
            /** Context */
            ctx?: Record<string, never>;
            /** Input */
            input?: unknown;
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    get_match_admin_v1_matches__match_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Идентификатор матча */
                match_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MatchResult"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не найдено */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    list_rooms_admin_v1_rooms_get: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
                phase?: ("lobby" | "countdown" | "level_open" | "level_closed" | "reveal" | "summary" | "paused" | "closed") | null;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AdminRoomsPage"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    abort_room_admin_v1_rooms__code__abort_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description Код комнаты: 8 символов `[A-HJ-NP-Z2-9]` (без 0/O/1/I) */
                code: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AbortRoomRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommandAccepted"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не найдено */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Конфликт состояния */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    get_match_v1_matches__match_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Идентификатор матча */
                match_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MatchResult"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не найдено */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    rate_v1_matches__match_id__rate_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description Идентификатор матча */
                match_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не найдено */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Конфликт состояния */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Слишком много запросов */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    my_matches_v1_me_matches_get: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MyMatchesPage"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    meta_v1_meta_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ServiceMeta"];
                };
            };
        };
    };
    queue_enter_v1_queue_enter_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["QueueEnterRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QueueTicket"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не найдено */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Конфликт состояния */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Слишком много запросов */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Зависимость временно недоступна */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    queue_leave_v1_queue_leave_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Конфликт состояния */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description Слишком много запросов */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    queue_pool_v1_queue_pool_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QueuePool"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    queue_tick_v1_queue_tick_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["QueueTicket"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не найдено */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Слишком много запросов */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    create_room_v1_rooms_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreateRoomRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RoomView"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не найдено */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Конфликт состояния */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Слишком много запросов */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Зависимость временно недоступна */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    get_room_v1_rooms__code__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Код комнаты: 8 символов `[A-HJ-NP-Z2-9]` (без 0/O/1/I) */
                code: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RoomView"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не найдено */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Слишком много запросов */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    join_v1_rooms__code__join_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Код комнаты: 8 символов `[A-HJ-NP-Z2-9]` (без 0/O/1/I) */
                code: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["JoinRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RoomCommandResult"];
                };
            };
            /** @description Команда принята в поток комнаты, владелец не ответил вовремя (D6) */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommandAccepted"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не найдено */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Конфликт состояния */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Слишком много запросов */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Зависимость временно недоступна */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    kick_v1_rooms__code__kick_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Код комнаты: 8 символов `[A-HJ-NP-Z2-9]` (без 0/O/1/I) */
                code: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TargetPlayerRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RoomCommandResult"];
                };
            };
            /** @description Команда принята в поток комнаты, владелец не ответил вовремя (D6) */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommandAccepted"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не найдено */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Конфликт состояния */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Слишком много запросов */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Зависимость временно недоступна */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    leave_v1_rooms__code__leave_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Код комнаты: 8 символов `[A-HJ-NP-Z2-9]` (без 0/O/1/I) */
                code: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LeaveRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RoomCommandResult"];
                };
            };
            /** @description Команда принята в поток комнаты, владелец не ответил вовремя (D6) */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommandAccepted"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не найдено */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Конфликт состояния */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Слишком много запросов */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Зависимость временно недоступна */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    away_v1_rooms__code__match_away_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Код комнаты: 8 символов `[A-HJ-NP-Z2-9]` (без 0/O/1/I) */
                code: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AwayRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MatchCommandResult"];
                };
            };
            /** @description Команда принята в поток комнаты, владелец не ответил вовремя (D6) */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommandAccepted"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не найдено */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Конфликт состояния */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Слишком много запросов */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Зависимость временно недоступна */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    chat_v1_rooms__code__match_chat_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Код комнаты: 8 символов `[A-HJ-NP-Z2-9]` (без 0/O/1/I) */
                code: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ChatRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MatchCommandResult"];
                };
            };
            /** @description Команда принята в поток комнаты, владелец не ответил вовремя (D6) */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommandAccepted"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не найдено */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Конфликт состояния */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Слишком много запросов */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Зависимость временно недоступна */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    emote_v1_rooms__code__match_emote_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Код комнаты: 8 символов `[A-HJ-NP-Z2-9]` (без 0/O/1/I) */
                code: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EmoteRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MatchCommandResult"];
                };
            };
            /** @description Команда принята в поток комнаты, владелец не ответил вовремя (D6) */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommandAccepted"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не найдено */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Конфликт состояния */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Слишком много запросов */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Зависимость временно недоступна */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    forfeit_v1_rooms__code__match_forfeit_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Код комнаты: 8 символов `[A-HJ-NP-Z2-9]` (без 0/O/1/I) */
                code: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ForfeitRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MatchCommandResult"];
                };
            };
            /** @description Команда принята в поток комнаты, владелец не ответил вовремя (D6) */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommandAccepted"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не найдено */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Конфликт состояния */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Слишком много запросов */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Зависимость временно недоступна */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    guess_v1_rooms__code__match_guess_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Код комнаты: 8 символов `[A-HJ-NP-Z2-9]` (без 0/O/1/I) */
                code: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GuessRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["GuessResult"];
                };
            };
            /** @description Команда принята в поток комнаты, владелец не ответил вовремя (D6) */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommandAccepted"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не найдено */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Конфликт состояния */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Слишком много запросов */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Зависимость временно недоступна */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    skip_v1_rooms__code__match_skip_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Код комнаты: 8 символов `[A-HJ-NP-Z2-9]` (без 0/O/1/I) */
                code: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SkipRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MatchCommandResult"];
                };
            };
            /** @description Команда принята в поток комнаты, владелец не ответил вовремя (D6) */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommandAccepted"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не найдено */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Конфликт состояния */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Слишком много запросов */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Зависимость временно недоступна */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    ready_v1_rooms__code__ready_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Код комнаты: 8 символов `[A-HJ-NP-Z2-9]` (без 0/O/1/I) */
                code: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ReadyRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RoomCommandResult"];
                };
            };
            /** @description Команда принята в поток комнаты, владелец не ответил вовремя (D6) */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommandAccepted"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не найдено */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Конфликт состояния */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Слишком много запросов */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Зависимость временно недоступна */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    realtime_token_v1_rooms__code__realtime_token_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Код комнаты: 8 символов `[A-HJ-NP-Z2-9]` (без 0/O/1/I) */
                code: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RealtimeSubscription"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не найдено */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Слишком много запросов */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    rematch_v1_rooms__code__rematch_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Код комнаты: 8 символов `[A-HJ-NP-Z2-9]` (без 0/O/1/I) */
                code: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RematchRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RematchResult"];
                };
            };
            /** @description Команда принята в поток комнаты, владелец не ответил вовремя (D6) */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommandAccepted"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не найдено */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Конфликт состояния */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Слишком много запросов */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Зависимость временно недоступна */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    reset_memory_v1_rooms__code__reset_memory_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Код комнаты: 8 символов `[A-HJ-NP-Z2-9]` (без 0/O/1/I) */
                code: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ResetMemoryRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RoomCommandResult"];
                };
            };
            /** @description Команда принята в поток комнаты, владелец не ответил вовремя (D6) */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommandAccepted"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не найдено */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Конфликт состояния */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Слишком много запросов */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Зависимость временно недоступна */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    change_settings_v1_rooms__code__settings_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Код комнаты: 8 символов `[A-HJ-NP-Z2-9]` (без 0/O/1/I) */
                code: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SettingsRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RoomCommandResult"];
                };
            };
            /** @description Команда принята в поток комнаты, владелец не ответил вовремя (D6) */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommandAccepted"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не найдено */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Конфликт состояния */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Слишком много запросов */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Зависимость временно недоступна */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    start_v1_rooms__code__start_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Код комнаты: 8 символов `[A-HJ-NP-Z2-9]` (без 0/O/1/I) */
                code: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["StartRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RoomCommandResult"];
                };
            };
            /** @description Команда принята в поток комнаты, владелец не ответил вовремя (D6) */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommandAccepted"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не найдено */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Конфликт состояния */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Слишком много запросов */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Зависимость временно недоступна */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    room_state_v1_rooms__code__state_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Код комнаты: 8 символов `[A-HJ-NP-Z2-9]` (без 0/O/1/I) */
                code: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RoomState"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не найдено */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Слишком много запросов */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    tick_v1_rooms__code__tick_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Код комнаты: 8 символов `[A-HJ-NP-Z2-9]` (без 0/O/1/I) */
                code: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TickRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TickResult"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не найдено */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Слишком много запросов */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    transfer_host_v1_rooms__code__transfer_host_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Код комнаты: 8 символов `[A-HJ-NP-Z2-9]` (без 0/O/1/I) */
                code: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TargetPlayerRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RoomCommandResult"];
                };
            };
            /** @description Команда принята в поток комнаты, владелец не ответил вовремя (D6) */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommandAccepted"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не найдено */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Конфликт состояния */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Слишком много запросов */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Зависимость временно недоступна */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    open_rooms_v1_rooms_open_get: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["OpenRoomsPage"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Не реализовано */
            501: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
    spectate_rooms_v1_rooms_spectate_get: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SpectateRoomsPage"];
                };
            };
            /** @description Не аутентифицирован */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Недостаточно прав */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Некорректные данные */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
            /** @description Слишком много запросов */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Problem"];
                    "application/problem+json": components["schemas"]["Problem"];
                };
            };
        };
    };
}
