/* eslint-disable */
// Сгенерировано `pnpm gen:api` из contracts/openapi/bff_web.json. Не править руками.

export interface paths {
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
    "/v1/pages/collections": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Каталог сборников
         * @description Без `q` и `cursor` — витрины `featured`, `popular`, `new` с общими фильтрами (`results: null`; публично, `public, max-age`). С `q` или `cursor` — только `results` в сортировке `sort` (витрины `null`); с `q` — `private, no-store`. Поиск — по названию, описанию и тегам. Упавший collections — блоки `unavailable`, страница 200; курсор не от этой сортировки — 422 от collections.
         */
        get: operations["collections_page_v1_pages_collections_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/pages/collections/{collection_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Страница сборника
         * @description Карточка (collections; `items` — только автору, админу и прошедшему, D6), режимы с играбельностью (game; недоступен — `modes: unavailable`, карточка отдаётся), `me` — только пользователю (гостю и анониму — null). Нет, скрыт для чужих или удалён — 404 `collections.not_found`. `comments` (5c, social) — первая страница комментариев, читают и анонимы; `can_edit`/`can_delete` — только с токеном пользователя. С токеном — `private, no-store`.
         */
        get: operations["collection_page_v1_pages_collections__collection_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/pages/events/{event_key}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Сезонное событие: карта, находки, дневник
         * @description Только пользователь с аккаунтом (аноним — 401, гость — 403), `private, no-store`. Данные — progression `GET /v1/events/{key}` + `theme.key` запуска; нет события — 404 `progression.event_not_found`; progression недоступен — 503. Засчитать находку-элемент интерфейса браузер просит прямо в progression (`POST /v1/events/{key}/finds/{find}/claim`).
         */
        get: operations["event_page_v1_pages_events__event_key__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/pages/home": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Главная: дейлики и режимы */
        get: operations["home_page_v1_pages_home_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/pages/leaderboards": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Лидерборды
         * @description Публичная страница: ladder-ы с сезоном и званиями, лидерборд (`ladder`, `cursor`; без `ladder` — первый из списка) и топ недели (rating). Блоки деградируют независимо. Без `around` ответ общий: `public, max-age=10`. `around=me` — окно вокруг пользователя и строки `me`: нужен вход (иначе 401), ответ `private, no-store`; в калибровке — обычная первая страница. Нет ladder-а — 404 `rating.ladder_not_found`, чужой курсор — 422.
         */
        get: operations["leaderboards_page_v1_pages_leaderboards_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/pages/library": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Медиатека: полки
         * @description Только пользователь: без токена — 401, гость — 403 `auth.guest_forbidden`. Упавший library — `shelves: unavailable`, страница 200. Всегда `private, no-store`.
         */
        get: operations["library_page_v1_pages_library_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/pages/library/{shelf_ref}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Медиатека: полка
         * @description `shelf_ref` — `system:<key>` или uuid своей полки; чужая и несуществующая — 404 `library.shelf_not_found`; без токена — 401, гость — 403. Упавший library — `shelf: unavailable`. Всегда `private, no-store`.
         */
        get: operations["library_shelf_page_v1_pages_library__shelf_ref__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/pages/match/{match_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Итог матча
         * @description Итог (match) + карточки единиц раундов (catalog) + флаги «в медиатеке» (library). Только пользователь: без токена — 401, гость — 403 `auth.guest_forbidden`. `players[].can_report` (5c) решает bff: зритель — участник несоло-матча, игрок — не он сам. Нет или ещё идёт — 404 `match.match_not_found`. Упавший match — все блоки `unavailable`; catalog или library — только их блок. Всегда `private, no-store`.
         */
        get: operations["match_page_v1_pages_match__match_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/pages/online": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Хаб онлайна
         * @description Блоки: открытые лобби и мои последние матчи (match), онлайн-режимы для создания комнаты (game), очереди рейтингового подбора (match), мои рейтинги, действующие сезоны и топ недели (rating), друзья и входящие заявки `friends` (social, 5c). Каждый блок деградирует независимо (`unavailable`, страница 200). Онлайн только с аккаунтом (D13): гостю и анониму — `status: auth_required`, `blocks: null`, match не вызывается. Токен не прошёл проверку или match ответил 401 — `status: session_expired` (обновить сессию и повторить). Всегда `private, no-store`.
         */
        get: operations["online_page_v1_pages_online_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/pages/players/{user_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Профиль игрока: уровень, опыт, бейджи
         * @description Публичная часть прогресса чужого (или своего) игрока. Только пользователь с аккаунтом: аноним — 401, гость — 403 `auth.guest_forbidden`, источники при этом не вызываются. Нет игрока — 404; недоступный progression — блок `unavailable`. 5c: блок `social` (отношение зрителя) и `restricted` (скрытый профиль, блокировка или неизвестный игрок — неразличимо; статистика не запрашивается). Всегда `private, no-store`.
         */
        get: operations["player_page_v1_pages_players__user_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/pages/profile": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Профиль: прогресс, кошелёк, задания, бейджи, колесо
         * @description Только пользователь с аккаунтом (аноним — 401, гость — 403), `private, no-store`. Блоки из progression гаснут независимо (страница 200); отказ токена источником — 401/403 экрана.
         */
        get: operations["profile_page_v1_pages_profile_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/pages/results/{session_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Экран результатов сессии
         * @description Только своя завершённая сессия. Чужая и несуществующая — одинаково 404; незавершённая — 409 (коды game).
         */
        get: operations["results_page_v1_pages_results__session_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/pages/shell": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Оболочка приложения: тема и колокольчик
         * @description Этап 5b. `sanctions` (5c) — действующие санкции пользователя. `theme.key` — ключ активной темы (тема активного сезонного события, иначе настройка bff `default_theme_key`; наборы токенов лежат во фронтенде, ADR 0054). `event` — баннер активного события; `found`/`total` — прогресс игрока (кэш до 30 с). `bell` — число непрочитанных уведомлений пользователя; аноним и гость — `null` без вызова notifications. Страница с токеном — `private, no-store`, без токена — публичный кэш.
         */
        get: operations["shell_page_v1_pages_shell_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/pages/shop": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Магазин: витрина косметики, паки
         * @description Этап 5b. Только пользователь с аккаунтом (аноним — 401, гость — 403), `private, no-store`. Блок `packs` — `null`, пока `billing.enabled=false`. Покупка — прямо в progression (`POST /api/progression/v1/shop/items/{id}/purchase`), bff только читает.
         */
        get: operations["shop_page_v1_pages_shop_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/pages/solo": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Хаб соло-режимов
         * @description Блоки: дейлики и недельная сетка субъекта, витрина режимов по группам (`source_type`, `presentation`), личные рекорды (только пользователю; гостю и анониму — `records: null`). Ответ с токеном — `private, no-store`.
         */
        get: operations["solo_page_v1_pages_solo_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/session": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Кто смотрит страницу
         * @description Недействительный или истёкший токен — `subject: null`, а не ошибка.
         */
        get: operations["session_v1_session_get"];
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
         * AppearanceCard
         * @description Оформление профиля: по одному предмету на слот (ник и аватар берёт фронт из блока игрока).
         */
        AppearanceCard: {
            /**
             * Custom Avatar Asset Id
             * @description Ассет одобренной аватарки: объект жалобы `avatar` (ADR 0069); null — аватарки нет или источник не отдал
             */
            custom_avatar_asset_id?: string | null;
            /**
             * Custom Avatar Url
             * @description Одобренная своя аватарка; null — аватар из слота
             */
            custom_avatar_url?: string | null;
            /** Slots */
            slots: components["schemas"]["AppearanceSlotCard"][];
        };
        /**
         * AppearanceSlotCard
         * @description Слот оформления: предмет или пусто.
         */
        AppearanceSlotCard: {
            item?: components["schemas"]["CosmeticCard"] | null;
            /** Slot */
            slot: string;
        };
        /**
         * AvatarCard
         * @description Своя аватарка: право, заявка, лимиты загрузки.
         */
        AvatarCard: {
            /** Entitled */
            entitled: boolean;
            /** Formats */
            formats: string[];
            /** Max Bytes */
            max_bytes: number;
            request?: components["schemas"]["AvatarRequestCard"] | null;
        };
        /**
         * AvatarRequestCard
         * @description Последняя заявка на свою аватарку.
         */
        AvatarRequestCard: {
            /** Reason Code */
            reason_code?: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "pending" | "approved" | "rejected";
            /**
             * Submitted At
             * Format: date-time
             */
            submitted_at: string;
        };
        /**
         * BadgeCard
         * @description Бейдж игрока.
         */
        BadgeCard: {
            /** Description */
            description: string;
            /** Key */
            key: string;
            /** Progress */
            progress: number;
            /** Target */
            target: number;
            /** Title */
            title: string;
            /** Unlocked At */
            unlocked_at?: string | null;
        };
        /**
         * BadgeRefCard
         * @description Полученный бейдж в публичном профиле игрока.
         */
        BadgeRefCard: {
            /** Key */
            key: string;
            /** Title */
            title: string;
            /**
             * Unlocked At
             * Format: date-time
             */
            unlocked_at: string;
        };
        /**
         * BellCard
         * @description Колокольчик: число непрочитанных уведомлений.
         */
        BellCard: {
            /** Unread */
            unread: number;
        };
        /** Block[AppearanceCard] */
        Block_AppearanceCard_: {
            data?: components["schemas"]["AppearanceCard"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[AvatarCard] */
        Block_AvatarCard_: {
            data?: components["schemas"]["AvatarCard"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[BellCard] */
        Block_BellCard_: {
            data?: components["schemas"]["BellCard"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[CollectionDetail] */
        Block_CollectionDetail_: {
            data?: components["schemas"]["CollectionDetail"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[CollectionResults] */
        Block_CollectionResults_: {
            data?: components["schemas"]["CollectionResults"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[CommentsCard] */
        Block_CommentsCard_: {
            data?: components["schemas"]["CommentsCard"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[FriendsCard] */
        Block_FriendsCard_: {
            data?: components["schemas"]["FriendsCard"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[HomeProgressCard] */
        Block_HomeProgressCard_: {
            data?: components["schemas"]["HomeProgressCard"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[InventoryCard] */
        Block_InventoryCard_: {
            data?: components["schemas"]["InventoryCard"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[LeaderboardCard] */
        Block_LeaderboardCard_: {
            data?: components["schemas"]["LeaderboardCard"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[list[BadgeCard]] */
        Block_list_BadgeCard__: {
            /** Data */
            data?: components["schemas"]["BadgeCard"][] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[list[CollectionModeCard]] */
        Block_list_CollectionModeCard__: {
            /** Data */
            data?: components["schemas"]["CollectionModeCard"][] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[list[DailyCard]] */
        Block_list_DailyCard__: {
            /** Data */
            data?: components["schemas"]["DailyCard"][] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[list[ItemCard]] */
        Block_list_ItemCard__: {
            /** Data */
            data?: components["schemas"]["ItemCard"][] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[list[ItemShelvesCard]] */
        Block_list_ItemShelvesCard__: {
            /** Data */
            data?: components["schemas"]["ItemShelvesCard"][] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[list[LadderCard]] */
        Block_list_LadderCard__: {
            /** Data */
            data?: components["schemas"]["LadderCard"][] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[list[ModeCard]] */
        Block_list_ModeCard__: {
            /** Data */
            data?: components["schemas"]["ModeCard"][] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[list[ModeGroup]] */
        Block_list_ModeGroup__: {
            /** Data */
            data?: components["schemas"]["ModeGroup"][] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[list[OnlineModeCard]] */
        Block_list_OnlineModeCard__: {
            /** Data */
            data?: components["schemas"]["OnlineModeCard"][] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[list[PackCard]] */
        Block_list_PackCard__: {
            /** Data */
            data?: components["schemas"]["PackCard"][] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[list[PlayerAppearanceCard]] */
        Block_list_PlayerAppearanceCard__: {
            /** Data */
            data?: components["schemas"]["PlayerAppearanceCard"][] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[list[QueuePoolCard]] */
        Block_list_QueuePoolCard__: {
            /** Data */
            data?: components["schemas"]["QueuePoolCard"][] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[list[RatingCard]] */
        Block_list_RatingCard__: {
            /** Data */
            data?: components["schemas"]["RatingCard"][] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[list[RecordCard]] */
        Block_list_RecordCard__: {
            /** Data */
            data?: components["schemas"]["RecordCard"][] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[list[SeasonCard]] */
        Block_list_SeasonCard__: {
            /** Data */
            data?: components["schemas"]["SeasonCard"][] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[list[ShelfCard]] */
        Block_list_ShelfCard__: {
            /** Data */
            data?: components["schemas"]["ShelfCard"][] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[list[WeeklyTopCard]] */
        Block_list_WeeklyTopCard__: {
            /** Data */
            data?: components["schemas"]["WeeklyTopCard"][] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[MatchSummary] */
        Block_MatchSummary_: {
            data?: components["schemas"]["MatchSummary"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[MyMatchesList] */
        Block_MyMatchesList_: {
            data?: components["schemas"]["MyMatchesList"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[OpenRoomsList] */
        Block_OpenRoomsList_: {
            data?: components["schemas"]["OpenRoomsList"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[PlayerSocialCard] */
        Block_PlayerSocialCard_: {
            data?: components["schemas"]["PlayerSocialCard"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[ProgressCard] */
        Block_ProgressCard_: {
            data?: components["schemas"]["ProgressCard"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[PublicProgressCard] */
        Block_PublicProgressCard_: {
            data?: components["schemas"]["PublicProgressCard"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[QuestsCard] */
        Block_QuestsCard_: {
            data?: components["schemas"]["QuestsCard"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[ResultSummary] */
        Block_ResultSummary_: {
            data?: components["schemas"]["ResultSummary"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[SanctionsCard] */
        Block_SanctionsCard_: {
            data?: components["schemas"]["SanctionsCard"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[ShelfItems] */
        Block_ShelfItems_: {
            data?: components["schemas"]["ShelfItems"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[ShopCatalogCard] */
        Block_ShopCatalogCard_: {
            data?: components["schemas"]["ShopCatalogCard"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[WalletCard] */
        Block_WalletCard_: {
            data?: components["schemas"]["WalletCard"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[WeeklyTopCard] */
        Block_WeeklyTopCard_: {
            data?: components["schemas"]["WeeklyTopCard"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /** Block[WheelCard] */
        Block_WheelCard_: {
            data?: components["schemas"]["WheelCard"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
        };
        /**
         * BoostCard
         * @description Действующий буст опыта.
         */
        BoostCard: {
            /** Matches Left */
            matches_left: number;
            /** Multiplier */
            multiplier: number;
        };
        /**
         * BracketPick
         * @description Выбор в сетке: тур и названия победителя и проигравшего.
         */
        BracketPick: {
            /** Loser */
            loser: string;
            /** Stage */
            stage: number;
            /** Winner */
            winner: string;
        };
        /**
         * BracketSummary
         * @description Итог сетки: чемпион и выборы по турам.
         */
        BracketSummary: {
            /** Champion */
            champion: string;
            /** Picks */
            picks: components["schemas"]["BracketPick"][];
        };
        /**
         * CollectionAuthor
         * @description Автор сборника (``collections``): пользователь, «Guessverse» или удалённый.
         */
        CollectionAuthor: {
            /**
             * Kind
             * @enum {string}
             */
            kind: "user" | "curator" | "deleted";
            /** Nickname */
            nickname?: string | null;
            /** User Id */
            user_id?: string | null;
        };
        /**
         * CollectionBlocks
         * @description Блоки страницы сборника: недоступный game даёт ``modes: unavailable``.
         */
        CollectionBlocks: {
            collection: components["schemas"]["Block_CollectionDetail_"];
            /** @description 5c: комментарии (social); null — не вызывалось */
            comments?: components["schemas"]["Block_CommentsCard_"] | null;
            modes: components["schemas"]["Block_list_CollectionModeCard__"];
        };
        /**
         * CollectionCard
         * @description Карточка сборника в каталоге (``collections /v1/collections``), без состава.
         */
        CollectionCard: {
            author: components["schemas"]["CollectionAuthor"];
            /**
             * Collection Id
             * Format: uuid
             */
            collection_id: string;
            cover?: components["schemas"]["CollectionCover"] | null;
            /** Default Mode Key */
            default_mode_key: string;
            /** Difficulty */
            difficulty?: ("easy" | "medium" | "hard") | null;
            /**
             * Domain
             * @enum {string}
             */
            domain: "music" | "film";
            /**
             * Featured
             * @default false
             */
            featured: boolean;
            /** Items Count */
            items_count: number;
            /**
             * Kind
             * @enum {string}
             */
            kind: "ugc" | "curated";
            /** Likes Count */
            likes_count: number;
            /** Plays Count */
            plays_count: number;
            /**
             * Published At
             * Format: date-time
             */
            published_at: string;
            /** Tags */
            tags: string[];
            /** Title */
            title: string;
        };
        /**
         * CollectionCover
         * @description Обложка — пресет (градиент и эмодзи из ``collections /v1/meta``).
         */
        CollectionCover: {
            /** Preset */
            preset: string;
        };
        /**
         * CollectionDetail
         * @description Сборник целиком (``collections /v1/collections/{id}``).
         */
        CollectionDetail: {
            author: components["schemas"]["CollectionAuthor"];
            /**
             * Collection Id
             * Format: uuid
             */
            collection_id: string;
            cover?: components["schemas"]["CollectionCover"] | null;
            /** Default Mode Key */
            default_mode_key: string;
            /** Description */
            description?: string | null;
            /** Difficulty */
            difficulty?: ("easy" | "medium" | "hard") | null;
            /**
             * Domain
             * @enum {string}
             */
            domain: "music" | "film";
            /**
             * Featured
             * @default false
             */
            featured: boolean;
            /** Items */
            items?: components["schemas"]["CollectionItemCard"][] | null;
            /** Items Count */
            items_count: number;
            /** Items Visible */
            items_visible: boolean;
            /**
             * Kind
             * @enum {string}
             */
            kind: "ugc" | "curated";
            /** Likes Count */
            likes_count: number;
            /** Plays Count */
            plays_count: number;
            /**
             * Published At
             * Format: date-time
             */
            published_at: string;
            /**
             * Status
             * @enum {string}
             */
            status: "published" | "hidden";
            /** Tags */
            tags: string[];
            /** Title */
            title: string;
            /** Version */
            version: number;
            /**
             * Visibility
             * @enum {string}
             */
            visibility: "public" | "unlisted";
        };
        /**
         * CollectionItemCard
         * @description Единица состава: только если состав открыт субъекту (D6).
         */
        CollectionItemCard: {
            /** Available */
            available: boolean;
            /** @description Только автору */
            custom?: components["schemas"]["CustomClueCard"] | null;
            /**
             * Media Item Id
             * Format: uuid
             */
            media_item_id: string;
            /** Position */
            position: number;
            /** Title */
            title: string;
            /** Year */
            year?: number | null;
        };
        /**
         * CollectionMe
         * @description Отношение пользователя к сборнику; гостю и анониму — ``me: null``.
         */
        CollectionMe: {
            /** Completed */
            completed: boolean;
            /** Favorite */
            favorite: boolean;
            /**
             * Is Author
             * @default false
             */
            is_author: boolean;
            /** Liked */
            liked: boolean;
        };
        /**
         * CollectionModeCard
         * @description Режим сборника (``game /v1/collections/{id}/modes``).
         */
        CollectionModeCard: {
            /** Allow Guest */
            allow_guest: boolean;
            /** Available */
            available: boolean;
            /** Clue Type */
            clue_type: string;
            /** Mode Key */
            mode_key: string;
            /** Playable */
            playable: number;
            /** Round Options */
            round_options: (5 | 10 | 25 | "all")[];
            /** Title Key */
            title_key: string;
            /** Total */
            total: number;
        };
        /**
         * CollectionPage
         * @description ``GET /v1/pages/collections/{id}``.
         */
        CollectionPage: {
            blocks: components["schemas"]["CollectionBlocks"];
            me?: components["schemas"]["CollectionMe"] | null;
        };
        /**
         * CollectionResults
         * @description Страница карточек по курсору; курсор привязан к сортировке блока.
         */
        CollectionResults: {
            /** Items */
            items: components["schemas"]["CollectionCard"][];
            /**
             * Next Cursor
             * @description Продолжение: `GET /v1/pages/collections?sort=<сортировка блока>&cursor=…` (с теми же фильтрами) вернёт блок `results`; null — дальше пусто
             */
            next_cursor?: string | null;
        };
        /**
         * CollectionsBlocks
         * @description Блоки каталога: без ``q`` и ``cursor`` — витрины, иначе только ``results``.
         *
         *     Отсутствующий в этом виде страницы блок — ``null``.
         */
        CollectionsBlocks: {
            /** @description Рекомендуемые (sort=featured) */
            featured?: components["schemas"]["Block_CollectionResults_"] | null;
            /** @description Новые (sort=new) */
            new?: components["schemas"]["Block_CollectionResults_"] | null;
            /** @description Популярные (sort=popular) */
            popular?: components["schemas"]["Block_CollectionResults_"] | null;
            /** @description Поиск (`q`) или продолжение по `cursor` в сортировке `sort` */
            results?: components["schemas"]["Block_CollectionResults_"] | null;
        };
        /**
         * CollectionsPage
         * @description ``GET /v1/pages/collections``.
         */
        CollectionsPage: {
            blocks: components["schemas"]["CollectionsBlocks"];
        };
        /**
         * CommentCard
         * @description Комментарий к сборнику.
         */
        CommentCard: {
            /**
             * Author Id
             * Format: uuid
             */
            author_id: string;
            /** Author Nickname */
            author_nickname: string;
            /** Body */
            body: string;
            /** Can Delete */
            can_delete: boolean;
            /** Can Edit */
            can_edit: boolean;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Edited At */
            edited_at: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
        };
        /**
         * CommentsCard
         * @description Первая страница комментариев сборника; дальше - social ``/v1/collections/{id}/comments``.
         */
        CommentsCard: {
            /** Items */
            items: components["schemas"]["CommentCard"][];
            /** Next Cursor */
            next_cursor: string | null;
            /** Total */
            total: number;
        };
        /**
         * CosmeticCard
         * @description Предмет косметики (progression, 5b): картинка или анимация по ``media_kind``.
         */
        CosmeticCard: {
            /** Asset Url */
            asset_url: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Key */
            key: string;
            /** Level Required */
            level_required?: number | null;
            /**
             * Media Kind
             * @enum {string}
             */
            media_kind: "static" | "animated";
            /**
             * Poster Url
             * @description Статичный кадр для prefers-reduced-motion
             */
            poster_url: string | null;
            /** Price */
            price?: number | null;
            /** Rarity */
            rarity: string;
            /** Slot */
            slot: string;
            /** Title */
            title: string;
        };
        /**
         * CustomClueCard
         * @description Своя загадка единицы (эмодзи); приходит только автору и админу.
         */
        CustomClueCard: {
            /** Emoji */
            emoji?: string | null;
        };
        /**
         * DailyCard
         * @description Карточка планового режима периода + статус субъекта (``game /v1/modes`` + ``/v1/daily``).
         *
         *     Дейлик — период «день», недельная сетка — «неделя»; ``day`` — начало периода,
         *     ``next_reset_at`` — его конец.
         */
        DailyCard: {
            /**
             * Day
             * Format: date
             */
            day: string;
            /**
             * Domain
             * @enum {string}
             */
            domain: "music" | "film";
            /** Max Score */
            max_score: number;
            /** Mode Key */
            mode_key: string;
            /**
             * Next Reset At
             * Format: date-time
             */
            next_reset_at: string;
            /**
             * Presentation
             * @default score
             * @enum {string}
             */
            presentation: "score" | "streak" | "bracket";
            /** Score */
            score?: number | null;
            /** Session Id */
            session_id?: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "not_started" | "in_progress" | "finished";
            /** Title Key */
            title_key: string;
        };
        /**
         * EventArchiveCard
         * @description Запуск события в архиве игрока (5d).
         */
        EventArchiveCard: {
            /** Completed */
            completed: boolean;
            /**
             * Ends At
             * Format: date-time
             */
            ends_at: string;
            /** Found */
            found: number;
            /** Key */
            key: string;
            /**
             * Starts At
             * Format: date-time
             */
            starts_at: string;
            /**
             * Status
             * @enum {string}
             */
            status: "active" | "finished" | "stopped";
            /** Title */
            title: string;
            /** Total */
            total: number;
        };
        /**
         * EventBannerCard
         * @description Активное сезонное событие для шапки и главной (5d, ADR 0054).
         */
        EventBannerCard: {
            /**
             * Ends At
             * Format: date-time
             */
            ends_at: string;
            /**
             * Found
             * @description Найдено игроком; null - аноним
             */
            found?: number | null;
            /** Key */
            key: string;
            /** Title */
            title: string;
            /**
             * Total
             * @description Всего находок; null - аноним
             */
            total?: number | null;
        };
        /**
         * EventFindCard
         * @description Находка события с состоянием для игрока.
         */
        EventFindCard: {
            /** Diary */
            diary: string;
            /** Final */
            final: boolean;
            /** Found At */
            found_at?: string | null;
            /** Key */
            key: string;
            /** Number */
            number: number;
            /** Play Page */
            play_page?: string | null;
            reward: components["schemas"]["EventFindRewardCard"];
            /**
             * State
             * @enum {string}
             */
            state: "locked" | "open" | "found";
            /** Title */
            title: string;
            trigger: components["schemas"]["EventFindTriggerCard"];
            /** X Pct */
            x_pct: number;
            /** Y Pct */
            y_pct: number;
        };
        /**
         * EventFindRewardCard
         * @description Награда находки.
         */
        EventFindRewardCard: {
            /** Verses */
            verses: number;
            /** Xp */
            xp: number;
        };
        /**
         * EventFindTriggerCard
         * @description Условие открытия находки; у закрытой финальной `progress = null`.
         */
        EventFindTriggerCard: {
            /** Metric */
            metric?: string | null;
            /** Page Key */
            page_key?: string | null;
            /** Period */
            period?: ("day" | "event") | null;
            /** Progress */
            progress?: number | null;
            /** Target */
            target?: number | null;
            /**
             * Type
             * @enum {string}
             */
            type: "server" | "ui_spot";
        };
        /**
         * EventPageView
         * @description ``GET /v1/pages/events/{key}``: страница события + тема (5d). Только пользователь.
         */
        EventPageView: {
            /** Completed */
            completed: boolean;
            /** Description */
            description: string;
            /**
             * Ends At
             * Format: date-time
             */
            ends_at: string;
            /** Finds */
            finds: components["schemas"]["EventFindCard"][];
            /** Found */
            found: number;
            /** Key */
            key: string;
            /** Map Url */
            map_url?: string | null;
            reward_item?: components["schemas"]["EventRewardCard"] | null;
            /**
             * Starts At
             * Format: date-time
             */
            starts_at: string;
            /**
             * Status
             * @enum {string}
             */
            status: "active" | "finished" | "stopped";
            theme: components["schemas"]["ThemeCard"];
            /** Theme Key */
            theme_key: string;
            /** Title */
            title: string;
            /** Total */
            total: number;
        };
        /**
         * EventRewardCard
         * @description Основная награда события.
         */
        EventRewardCard: {
            /** Image Url */
            image_url: string;
            /** Item Key */
            item_key: string;
            /** Title */
            title: string;
        };
        /**
         * ExternalLink
         * @description Ссылка на внешний сервис (TMDB, MusicBrainz …).
         */
        ExternalLink: {
            /** Kind */
            kind: string;
            /** Url */
            url: string;
        };
        /**
         * FriendCard
         * @description Друг в блоке «друзья» (только владельцу).
         */
        FriendCard: {
            /** Nickname */
            nickname: string;
            /**
             * Status
             * @enum {string}
             */
            status: "online" | "in_match" | "offline";
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
        };
        /**
         * FriendsCard
         * @description Блок друзей для хаба онлайна и лобби (приглашение в комнату).
         */
        FriendsCard: {
            /** Incoming Requests */
            incoming_requests: number;
            /** Items */
            items: components["schemas"]["FriendCard"][];
        };
        /**
         * HomeBlocks
         * @description Блоки главной.
         */
        HomeBlocks: {
            /** @description Наборы дня (без недельной сетки) */
            dailies: components["schemas"]["Block_list_DailyCard__"];
            /** @description 5d: активное событие; null - нет события или progression недоступен */
            event?: components["schemas"]["EventBannerCard"] | null;
            modes: components["schemas"]["Block_list_ModeCard__"];
            /** @description Прогресс игрока; null — гость или аноним */
            progress?: components["schemas"]["Block_HomeProgressCard_"] | null;
            /** @description Наборы недели (сетка) */
            weekly: components["schemas"]["Block_list_DailyCard__"];
        };
        /**
         * HomePage
         * @description ``GET /v1/pages/home``.
         */
        HomePage: {
            blocks: components["schemas"]["HomeBlocks"];
        };
        /**
         * HomeProgressCard
         * @description Свёртка прогресса для главной: уровень, серия, колесо.
         */
        HomeProgressCard: {
            /**
             * Best Streak
             * @description Наибольшая текущая серия дейлика по доменам
             */
            best_streak: number;
            /** Can Spin */
            can_spin: boolean;
            /** Level */
            level: number;
            /**
             * Quests Open
             * @description Невыполненных заданий на сегодня
             */
            quests_open: number;
            /** Xp In Level */
            xp_in_level: number;
            /** Xp To Next */
            xp_to_next: number | null;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /**
         * InventoryCard
         * @description Инвентарь игрока.
         */
        InventoryCard: {
            /** Items */
            items: components["schemas"]["InventoryEntryCard"][];
        };
        /**
         * InventoryEntryCard
         * @description Предмет инвентаря.
         */
        InventoryEntryCard: {
            /** Equipped */
            equipped: boolean;
            /** Expires At */
            expires_at?: string | null;
            item: components["schemas"]["CosmeticCard"];
            /** Source */
            source: string;
        };
        /**
         * ItemCard
         * @description Карточка единицы из ``catalog /v1/items/{id}``.
         */
        ItemCard: {
            /**
             * Domain
             * @enum {string}
             */
            domain: "music" | "film";
            /** External Links */
            external_links?: components["schemas"]["ExternalLink"][];
            /** Genres */
            genres: string[];
            /** Kind */
            kind: string;
            /**
             * Media Item Id
             * Format: uuid
             */
            media_item_id: string;
            /** Original Title */
            original_title?: string | null;
            /** Persons */
            persons: components["schemas"]["PersonCard"][];
            /** Title */
            title: string;
            /** Year */
            year?: number | null;
        };
        /**
         * ItemShelvesCard
         * @description Флаг «в медиатеке»: полки пользователя с единицей (пусто — не сохранена).
         */
        ItemShelvesCard: {
            /**
             * Media Item Id
             * Format: uuid
             */
            media_item_id: string;
            /** Shelves */
            shelves: string[];
        };
        JsonValue: unknown;
        /**
         * LadderCard
         * @description Ladder для переключателя на странице лидербордов.
         */
        LadderCard: {
            /**
             * Domain
             * @enum {string}
             */
            domain: "music" | "film";
            /** Formats */
            formats: ("classic" | "lite")[];
            /** Key */
            key: string;
            season?: components["schemas"]["SeasonCard"] | null;
            /** Tiers */
            tiers: components["schemas"]["TierCard"][];
        };
        /**
         * LeaderboardCard
         * @description Страница лидерборда; `next_cursor` — для `rating /v1/ladders/{key}/leaderboard`.
         */
        LeaderboardCard: {
            /** Items */
            items: components["schemas"]["LeaderboardEntryCard"][];
            /** Ladder */
            ladder: string;
            me?: components["schemas"]["LeaderboardEntryCard"] | null;
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /**
         * LeaderboardEntryCard
         * @description Строка лидерборда.
         */
        LeaderboardEntryCard: {
            /** Matches Played */
            matches_played: number;
            /** Nickname */
            nickname: string;
            /** Place */
            place: number;
            /** Rating */
            rating: number;
            tier: components["schemas"]["TierCard"];
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
        };
        /**
         * LeaderboardsBlocks
         * @description Блоки страницы лидербордов; каждый деградирует независимо.
         */
        LeaderboardsBlocks: {
            ladders: components["schemas"]["Block_list_LadderCard__"];
            leaderboard: components["schemas"]["Block_LeaderboardCard_"];
            weekly: components["schemas"]["Block_WeeklyTopCard_"];
        };
        /**
         * LeaderboardsPage
         * @description ``GET /v1/pages/leaderboards``: публичная страница, кэш 10 с (с ``around=me`` — личная).
         */
        LeaderboardsPage: {
            blocks: components["schemas"]["LeaderboardsBlocks"];
        };
        /**
         * LibraryBlocks
         * @description Блоки медиатеки.
         */
        LibraryBlocks: {
            shelves: components["schemas"]["Block_list_ShelfCard__"];
        };
        /**
         * LibraryPage
         * @description ``GET /v1/pages/library`` (только пользователь).
         */
        LibraryPage: {
            blocks: components["schemas"]["LibraryBlocks"];
        };
        /**
         * LibraryShelfBlocks
         * @description Блоки страницы полки.
         */
        LibraryShelfBlocks: {
            shelf: components["schemas"]["Block_ShelfItems_"];
        };
        /**
         * LibraryShelfPage
         * @description ``GET /v1/pages/library/{ref}`` (только пользователь).
         */
        LibraryShelfPage: {
            blocks: components["schemas"]["LibraryShelfBlocks"];
        };
        /**
         * MatchBlocks
         * @description Блоки экрана итога матча.
         */
        MatchBlocks: {
            /** @description Оформление игроков матча; null до потока S5 этапа 5b */
            appearance?: components["schemas"]["Block_list_PlayerAppearanceCard__"] | null;
            items: components["schemas"]["Block_list_ItemCard__"];
            library: components["schemas"]["Block_list_ItemShelvesCard__"];
            match: components["schemas"]["Block_MatchSummary_"];
        };
        /**
         * MatchModeCard
         * @description Шаблон режима комнаты или матча (``match ModeRef``).
         */
        MatchModeCard: {
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
         * MatchPage
         * @description ``GET /v1/pages/match/{match_id}`` (только пользователь).
         */
        MatchPage: {
            blocks: components["schemas"]["MatchBlocks"];
        };
        /**
         * MatchParticipantCard
         * @description Другой игрок матча в карточке истории (для жалобы).
         */
        MatchParticipantCard: {
            /** Can Report */
            can_report: boolean;
            /** Nickname */
            nickname: string;
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
        };
        /**
         * MatchPlayerCard
         * @description Игрок в итоге матча: место с ничьими (1, 1, 3), ``outcome`` — только у дуэли.
         */
        MatchPlayerCard: {
            /**
             * Can Add Friend
             * @description ADR 0069: можно позвать в друзья (social `can_send_request` по участникам пачкой); social недоступен — false
             * @default false
             */
            can_add_friend: boolean;
            /**
             * Can Report
             * @description 5c: показывать кнопку жалобы (не сам игрок, матч не соло); решает bff
             * @default false
             */
            can_report: boolean;
            /**
             * Finish
             * @enum {string}
             */
            finish: "completed" | "forfeit" | "left" | "tech_win" | "tech_loss";
            /** Nickname */
            nickname: string;
            /** Outcome */
            outcome: ("win" | "loss" | "draw") | null;
            /** Place */
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
         * MatchRoundCard
         * @description Раунд итога: карточка единицы — в блоке ``items`` по ``media_item_id``.
         */
        MatchRoundCard: {
            /** Index */
            index: number;
            /**
             * Media Item Id
             * Format: uuid
             */
            media_item_id: string;
            /** Results */
            results: components["schemas"]["RoundPlayerResultCard"][];
            /**
             * Void
             * @description Раунд аннулирован (ассет недоступен)
             * @default false
             */
            void: boolean;
        };
        /**
         * MatchSummary
         * @description Итог матча (``match /v1/matches/{id}``).
         */
        MatchSummary: {
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
            mode: components["schemas"]["MatchModeCard"];
            /** Players */
            players: components["schemas"]["MatchPlayerCard"][];
            /** Room Code */
            room_code: string;
            /** Rounds */
            rounds: components["schemas"]["MatchRoundCard"][];
            /**
             * Started At
             * Format: date-time
             */
            started_at: string;
        };
        /**
         * ModeCard
         * @description Карточка режима (из ``game /v1/modes``).
         */
        ModeCard: {
            /** Allow Guest */
            allow_guest: boolean;
            /**
             * Answer Format
             * @description Реестр ответов фронта
             * @default classic
             * @enum {string}
             */
            answer_format: "classic" | "pick";
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
             * Presentation
             * @description Реестр итогов фронта
             * @default score
             * @enum {string}
             */
            presentation: "score" | "streak" | "bracket";
            /** Presets */
            presets?: components["schemas"]["PresetCard"][] | null;
            /**
             * Rounds
             * @description Число раундов; у марафона и пары — максимум
             */
            rounds: number;
            /** Source Type */
            source_type: string;
            /** @description null — выбирать нечего */
            start?: components["schemas"]["ModeStartCard"] | null;
            /** Title Key */
            title_key: string;
        };
        /**
         * ModeGroup
         * @description Группа витрины: режимы с одинаковыми ``source_type`` и ``presentation``.
         */
        ModeGroup: {
            /** Modes */
            modes: components["schemas"]["ModeCard"][];
            /**
             * Presentation
             * @enum {string}
             */
            presentation: "score" | "streak" | "bracket";
            /** Source Type */
            source_type: string;
        };
        /**
         * ModeStartCard
         * @description Что игрок выбирает перед стартом режима.
         */
        ModeStartCard: {
            subject?: components["schemas"]["SubjectPickCard"] | null;
        };
        /**
         * MyMatchCard
         * @description Матч в «Моих матчах» (``match /v1/me/matches``).
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
            mode: components["schemas"]["MatchModeCard"];
            /**
             * My Finish
             * @enum {string}
             */
            my_finish: "completed" | "forfeit" | "left" | "tech_win" | "tech_loss";
            /**
             * Outcome
             * @description Только у дуэли
             */
            outcome: ("win" | "loss" | "draw") | null;
            /**
             * Participants
             * @description ADR 0069: другие игроки матча для жалобы из истории; пусто — игроки не получены (источник недоступен, лимит `online_match_players_limit` исчерпан)
             */
            participants?: components["schemas"]["MatchParticipantCard"][];
            /** Place */
            place: number;
            /** Players */
            players: number;
            /** Score */
            score: number;
        };
        /**
         * MyMatchesList
         * @description Мои последние матчи; ``next_cursor`` — для ``match /v1/me/matches``.
         */
        MyMatchesList: {
            /** Items */
            items: components["schemas"]["MyMatchCard"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /**
         * OnlineBlocks
         * @description Блоки хаба онлайна: каждый деградирует независимо.
         */
        OnlineBlocks: {
            /** @description 5c: друзья со статусом и число входящих заявок (social); null — не вызывалось */
            friends?: components["schemas"]["Block_FriendsCard_"] | null;
            /** @description Онлайн-режимы для создания комнаты (`game /v1/modes?session=online`) */
            modes: components["schemas"]["Block_list_OnlineModeCard__"];
            my_matches: components["schemas"]["Block_MyMatchesList_"];
            open_rooms: components["schemas"]["Block_OpenRoomsList_"];
            /** @description 4b: сколько игроков ищет матч (`match /v1/queue/pool`) */
            queues: components["schemas"]["Block_list_QueuePoolCard__"];
            /** @description 4b: мои звания и калибровка (`rating /v1/me/ratings`); в калибровке `rating`/`tier` — null, «осталось N матчей» — `calibration_left` */
            ratings: components["schemas"]["Block_list_RatingCard__"];
            /** @description 4b: действующие сезоны (ladder без сезона пропускается) */
            seasons: components["schemas"]["Block_list_SeasonCard__"];
            /** @description 4b: топ недели по каждому ladder-у (`rating /v1/ladders/{key}/weekly`) */
            weekly: components["schemas"]["Block_list_WeeklyTopCard__"];
        };
        /**
         * OnlineChatCard
         * @description Эмоции и готовые фразы (ключи i18n ``match.emotes.*``/``match.phrases.*``).
         */
        OnlineChatCard: {
            /** Emotes */
            emotes: string[];
            /**
             * Free Text
             * @description Свободный чат — только кастомное лобби (ADR 0036)
             */
            free_text: boolean;
            /** Phrases */
            phrases: string[];
        };
        /**
         * OnlineModeCard
         * @description Онлайн-режим для создания комнаты (``game /v1/modes?session=online``).
         */
        OnlineModeCard: {
            /**
             * Answer Format
             * @enum {string}
             */
            answer_format: "classic" | "pick";
            /** Clue Type */
            clue_type: string;
            /**
             * Domain
             * @enum {string}
             */
            domain: "music" | "film";
            /** Key */
            key: string;
            online: components["schemas"]["OnlineRulesCard"];
            /**
             * Presentation
             * @enum {string}
             */
            presentation: "score" | "streak" | "bracket";
            /** Source Type */
            source_type: string;
            /** Title Key */
            title_key: string;
            /** Version */
            version: number;
        };
        /**
         * OnlinePage
         * @description ``GET /v1/pages/online``: блоки — только при ``status = ok``, иначе ``blocks: null``.
         */
        OnlinePage: {
            blocks?: components["schemas"]["OnlineBlocks"] | null;
            /**
             * Status
             * @description `auth_required` — гость или аноним (экран регистрации, D13); `session_expired` — токен не прошёл проверку или match ответил 401 (обновить сессию и повторить)
             * @enum {string}
             */
            status: "ok" | "auth_required" | "session_expired";
        };
        /**
         * OnlineRulesCard
         * @description Правила комнаты онлайн-режима (``game ModeOut.online``, ADR 0032).
         */
        OnlineRulesCard: {
            chat: components["schemas"]["OnlineChatCard"];
            /**
             * Contest Type
             * @description Плагин состязания (`race`, `parallel`)
             */
            contest_type: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "duel" | "party" | "lobby";
            /** Ladder */
            ladder?: string | null;
            /**
             * Level Windows S
             * @description Окно каждого уровня загадки, с
             */
            level_windows_s: number[];
            /**
             * Lobby
             * @description Только у кастомного лобби: `{visibility, overridable}` (LobbySpec конфига)
             */
            lobby?: {
                [key: string]: components["schemas"]["JsonValue"];
            } | null;
            /**
             * Max Collections
             * @description Только у лобби по сборнику: сколько сборников выбрать
             */
            max_collections?: number | null;
            /** Players Max */
            players_max: number;
            /** Players Min */
            players_min: number;
            /** Ranked */
            ranked: boolean;
            /** Rounds */
            rounds: number;
        };
        /**
         * OpenRoomCard
         * @description Открытое лобби (``match /v1/rooms/open``): вход — по ``code`` напрямую в match.
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
            mode: components["schemas"]["MatchModeCard"];
            /** Players */
            players: number;
            /** Players Max */
            players_max: number;
            settings: components["schemas"]["RoomSettingsCard"];
        };
        /**
         * OpenRoomsList
         * @description Открытые лобби, новые первыми; ``next_cursor`` — для ``match /v1/rooms/open``.
         */
        OpenRoomsList: {
            /** Items */
            items: components["schemas"]["OpenRoomCard"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /**
         * PackCard
         * @description Пак косметики (виден только при ``billing.enabled=true``).
         */
        PackCard: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Items */
            items: components["schemas"]["CosmeticCard"][];
            /** Key */
            key: string;
            /** Price Kopecks */
            price_kopecks: number;
            /** Style */
            style: string;
            /** Title */
            title: string;
            /** Verses Amount */
            verses_amount: number;
        };
        /**
         * PersonCard
         * @description Персона в карточке единицы.
         */
        PersonCard: {
            /** Name */
            name: string;
            /** Role */
            role: string;
        };
        /**
         * PlayerAppearanceCard
         * @description Оформление игрока в матче: ник, аватар, рамка, титул (по ``user_id``).
         */
        PlayerAppearanceCard: {
            appearance: components["schemas"]["AppearanceCard"];
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
        };
        /**
         * PlayerBlocks
         * @description Блоки профиля игрока.
         */
        PlayerBlocks: {
            /** @description Оформление игрока; null до потока S5 этапа 5b */
            appearance?: components["schemas"]["Block_AppearanceCard_"] | null;
            progress: components["schemas"]["Block_PublicProgressCard_"];
            /** @description Друзья и блокировка; null — не вызывалось (5c, поток F5) */
            social?: components["schemas"]["Block_PlayerSocialCard_"] | null;
        };
        /**
         * PlayerPage
         * @description ``GET /v1/pages/players/{user_id}``: только пользователь с аккаунтом, `private, no-store`.
         */
        PlayerPage: {
            blocks: components["schemas"]["PlayerBlocks"];
            /**
             * Nickname
             * @description Ник игрока из social (ADR 0069); null — профиль скрыт, свой профиль или social недоступен: фронт показывает нейтральное «Игрок»
             */
            nickname?: string | null;
            /**
             * Restricted
             * @description 5c: профиль скрыт, игрок заблокировал зрителя или неизвестен — показаны только ник, аватар и оформление, `progress` = `unavailable`; ответы неразличимы (нет перечисления)
             * @default false
             */
            restricted: boolean;
        };
        /**
         * PlayerSocialCard
         * @description Отношение зрителя к игроку (social ``/v1/players/{id}/relation``, 5c).
         */
        PlayerSocialCard: {
            /** Blocked By Me */
            blocked_by_me: boolean;
            /** Can Send Request */
            can_send_request: boolean;
            /** Is Friend */
            is_friend: boolean;
        };
        /**
         * PresetCard
         * @description Пресет случайного режима.
         */
        PresetCard: {
            /** Id */
            id: string;
            /** Title Key */
            title_key: string;
        };
        /**
         * PrizeCard
         * @description Приз сектора колеса: тип и параметры из конфига progression.
         */
        PrizeCard: {
            /** Amount */
            amount: number;
            /** Item Key */
            item_key?: string | null;
            /**
             * Kind
             * @enum {string}
             */
            kind: "xp" | "verses" | "boost" | "item" | "none";
            /** Matches */
            matches?: number | null;
            /** Multiplier */
            multiplier?: number | null;
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
         * ProfileBlocks
         * @description Блоки профиля: каждый гаснет независимо.
         */
        ProfileBlocks: {
            /** @description Оформление; null до потока S5 этапа 5b */
            appearance?: components["schemas"]["Block_AppearanceCard_"] | null;
            /** @description Своя аватарка; null до потока S5 этапа 5b */
            avatar?: components["schemas"]["Block_AvatarCard_"] | null;
            badges: components["schemas"]["Block_list_BadgeCard__"];
            /**
             * Events
             * @description 5d: архив событий игрока (новые первыми); пусто - нет
             */
            events?: components["schemas"]["EventArchiveCard"][];
            /** @description Инвентарь; null до потока S5 этапа 5b */
            inventory?: components["schemas"]["Block_InventoryCard_"] | null;
            progress: components["schemas"]["Block_ProgressCard_"];
            quests: components["schemas"]["Block_QuestsCard_"];
            wallet: components["schemas"]["Block_WalletCard_"];
            wheel: components["schemas"]["Block_WheelCard_"];
        };
        /**
         * ProfilePage
         * @description ``GET /v1/pages/profile``: только пользователь с аккаунтом, ответ ``private, no-store``.
         */
        ProfilePage: {
            blocks: components["schemas"]["ProfileBlocks"];
        };
        /**
         * ProgressCard
         * @description Уровень и опыт игрока (progression ``/v1/me/progress``).
         */
        ProgressCard: {
            boost?: components["schemas"]["BoostCard"] | null;
            /** Level */
            level: number;
            /** Max Level */
            max_level: number;
            /**
             * Solo Xp Cap
             * @description Суточный потолок опыта из соло
             */
            solo_xp_cap: number;
            /**
             * Solo Xp Today
             * @description Опыт из соло за сегодня
             */
            solo_xp_today: number;
            /** Streaks */
            streaks: components["schemas"]["StreakCard"][];
            /** Total Xp */
            total_xp: number;
            /** Xp In Level */
            xp_in_level: number;
            /**
             * Xp To Next
             * @description null — высший уровень
             */
            xp_to_next: number | null;
        };
        /**
         * PublicProgressCard
         * @description Публичная часть прогресса игрока: уровень, опыт, бейджи.
         */
        PublicProgressCard: {
            /** Badges */
            badges: components["schemas"]["BadgeRefCard"][];
            /** Level */
            level: number;
            /** Total Xp */
            total_xp: number;
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
        };
        /**
         * QuestCard
         * @description Задание текущего периода.
         */
        QuestCard: {
            /** Completed */
            completed: boolean;
            /** Description */
            description: string;
            /** Key */
            key: string;
            /** Progress */
            progress: number;
            /** Reward Verses */
            reward_verses: number;
            /** Reward Xp */
            reward_xp: number;
            /** Target */
            target: number;
            /** Title */
            title: string;
        };
        /**
         * QuestsCard
         * @description Задания игрока: дневные и недельные.
         */
        QuestsCard: {
            /** Daily */
            daily: components["schemas"]["QuestCard"][];
            /** Weekly */
            weekly: components["schemas"]["QuestCard"][];
        };
        /**
         * QueuePoolCard
         * @description Очередь ladder-а и формата: сколько игроков ищет матч (ADR 0038).
         */
        QueuePoolCard: {
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
         * RatingCard
         * @description Рейтинг пользователя в ladder-е; в калибровке `rating` и `tier` — null (ADR 0037).
         */
        RatingCard: {
            /** Calibrating */
            calibrating: boolean;
            /**
             * Calibration Left
             * @description «Осталось N матчей»
             */
            calibration_left: number;
            /** Ladder */
            ladder: string;
            /** Matches Played */
            matches_played: number;
            /** Next Tier At */
            next_tier_at?: number | null;
            /** Place */
            place?: number | null;
            /** Rating */
            rating: number | null;
            tier: components["schemas"]["TierCard"] | null;
        };
        /**
         * RecordCard
         * @description Личный рекорд режима (``game /v1/me/records``).
         */
        RecordCard: {
            /**
             * Achieved At
             * Format: date-time
             */
            achieved_at: string;
            /** Best */
            best: number;
            /**
             * Domain
             * @enum {string}
             */
            domain: "music" | "film";
            /** Mode Key */
            mode_key: string;
            /** Percentile */
            percentile?: number | null;
            /**
             * Presentation
             * @enum {string}
             */
            presentation: "score" | "streak" | "bracket";
            /** Title Key */
            title_key: string;
        };
        /**
         * ResultRecord
         * @description Рекорд в итоге (только пользователи, режимы с рекордами).
         */
        ResultRecord: {
            /** Best */
            best: number;
            /** Is New */
            is_new: boolean;
            /** Percentile */
            percentile?: number | null;
        };
        /**
         * ResultRoundCard
         * @description Раунд итога (из ``game /v1/sessions/{id}/result``).
         */
        ResultRoundCard: {
            /**
             * Exact Pct
             * @description Доля угадавших (только дейлик)
             */
            exact_pct?: number | null;
            /** Index */
            index: number;
            /** Level */
            level: number | null;
            /**
             * Media Item Id
             * @description null у раунда void
             */
            media_item_id: string | null;
            /**
             * Outcome
             * @enum {string}
             */
            outcome: "exact" | "partial" | "miss" | "gave_up" | "void";
            /** Points */
            points: number;
        };
        /**
         * ResultsBlocks
         * @description Блоки экрана результатов.
         */
        ResultsBlocks: {
            items: components["schemas"]["Block_list_ItemCard__"];
            result: components["schemas"]["Block_ResultSummary_"];
        };
        /**
         * ResultsPage
         * @description ``GET /v1/pages/results/{session_id}``.
         */
        ResultsPage: {
            blocks: components["schemas"]["ResultsBlocks"];
        };
        /**
         * ResultSummary
         * @description Итог сессии для экрана результатов.
         */
        ResultSummary: {
            /** @description Только у сетки */
            bracket?: components["schemas"]["BracketSummary"] | null;
            /** Max Score */
            max_score: number;
            /** Mode Key */
            mode_key?: string | null;
            /**
             * Presentation
             * @description Реестр итогов фронта
             * @default score
             * @enum {string}
             */
            presentation: "score" | "streak" | "bracket";
            /** @description null — гость или без рекордов */
            record?: components["schemas"]["ResultRecord"] | null;
            /** Rounds */
            rounds: components["schemas"]["ResultRoundCard"][];
            /** Score */
            score: number;
            /**
             * Session Id
             * Format: uuid
             */
            session_id: string;
            /** Share Text */
            share_text: string;
        };
        /**
         * RoomSettingsCard
         * @description Действующие настройки комнаты (шаблон + переопределения хоста).
         */
        RoomSettingsCard: {
            /** Answer Format */
            answer_format: string;
            /** Clue Type */
            clue_type: string;
            /** Collection Ids */
            collection_ids?: string[];
            /**
             * Level Window S
             * @description Окно уровня, с
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
         * RoundPlayerResultCard
         * @description Результат игрока в раунде.
         */
        RoundPlayerResultCard: {
            /**
             * Elapsed Ms
             * @description От открытия уровня
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
         * SanctionCard
         * @description Действующая санкция игрока.
         */
        SanctionCard: {
            /**
             * Expires At
             * @description null - бессрочно
             */
            expires_at: string | null;
            /**
             * Kind
             * @enum {string}
             */
            kind: "ranked" | "chat" | "online" | "account";
            /** Reason */
            reason: string;
        };
        /**
         * SanctionsCard
         * @description Блок санкций оболочки (экран санкции); пусто - ограничений нет.
         */
        SanctionsCard: {
            /** Items */
            items: components["schemas"]["SanctionCard"][];
        };
        /**
         * SeasonCard
         * @description Действующий сезон ladder-а.
         */
        SeasonCard: {
            /**
             * Ends At
             * Format: date-time
             * @description «Осталось X д.» считает клиент
             */
            ends_at: string;
            /** Ladder */
            ladder: string;
            /** Number */
            number: number;
            /**
             * Starts At
             * Format: date-time
             */
            starts_at: string;
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
         * SessionInfo
         * @description ``GET /v1/session``: ``subject = null`` — нет токена или он недействителен.
         */
        SessionInfo: {
            subject: components["schemas"]["Subject"] | null;
        };
        /**
         * ShelfCard
         * @description Полка (``library /v1/shelves``).
         */
        ShelfCard: {
            /** Domains */
            domains: ("music" | "film")[];
            /** Exclusive With */
            exclusive_with?: string[];
            /** Items Count */
            items_count: number;
            /** Key */
            key?: string | null;
            /**
             * Kind
             * @enum {string}
             */
            kind: "system" | "custom";
            /** Ref */
            ref: string;
            /** Shelf Id */
            shelf_id?: string | null;
            /** Title */
            title?: string | null;
            /** Title Key */
            title_key?: string | null;
            /** Updated At */
            updated_at?: string | null;
        };
        /**
         * ShelfItemCard
         * @description Единица на полке: карточка из проекции catalog в library.
         */
        ShelfItemCard: {
            /**
             * Added At
             * Format: date-time
             */
            added_at: string;
            /**
             * Domain
             * @enum {string}
             */
            domain: "music" | "film";
            /** Kind */
            kind: string;
            /**
             * Media Item Id
             * Format: uuid
             */
            media_item_id: string;
            /** Persons */
            persons?: string[];
            /**
             * Source
             * @enum {string}
             */
            source: "reveal" | "results" | "card" | "collection" | "library";
            /**
             * Status
             * @enum {string}
             */
            status: "available" | "unavailable";
            /** Title */
            title: string;
            /** Year */
            year?: number | null;
        };
        /**
         * ShelfItems
         * @description Полка и её единицы (страница по курсору).
         */
        ShelfItems: {
            /** Items */
            items: components["schemas"]["ShelfItemCard"][];
            /** Next Cursor */
            next_cursor?: string | null;
            shelf: components["schemas"]["ShelfCard"];
        };
        /**
         * ShellPage
         * @description ``GET /v1/pages/shell``: оболочка приложения — тема и колокольчик.
         */
        ShellPage: {
            /** @description null — аноним или гость, notifications не вызывается */
            bell?: components["schemas"]["Block_BellCard_"] | null;
            /** @description 5d: активное событие; null - нет события или progression недоступен */
            event?: components["schemas"]["EventBannerCard"] | null;
            /** @description 5c: действующие санкции; null — аноним или гость */
            sanctions?: components["schemas"]["Block_SanctionsCard_"] | null;
            theme: components["schemas"]["ThemeCard"];
        };
        /**
         * ShopBlocks
         * @description Блоки витрины.
         */
        ShopBlocks: {
            catalog: components["schemas"]["Block_ShopCatalogCard_"];
            /** @description null — продажа выключена (billing.enabled=false) */
            packs?: components["schemas"]["Block_list_PackCard__"] | null;
        };
        /**
         * ShopCatalogCard
         * @description Витрина: предметы и баланс (покупка — прямо в progression, bff только читает).
         */
        ShopCatalogCard: {
            /** Balance */
            balance: number;
            /** Items */
            items: components["schemas"]["ShopEntryCard"][];
            /**
             * Slots
             * @description Слоты в порядке конфига — для вкладок витрины
             */
            slots: string[];
        };
        /**
         * ShopEntryCard
         * @description Предмет витрины с состоянием для игрока.
         */
        ShopEntryCard: {
            item: components["schemas"]["CosmeticCard"];
            /**
             * State
             * @enum {string}
             */
            state: "locked" | "available" | "owned";
        };
        /**
         * ShopPage
         * @description ``GET /v1/pages/shop``: только пользователь с аккаунтом, `private, no-store`.
         */
        ShopPage: {
            blocks: components["schemas"]["ShopBlocks"];
        };
        /**
         * SoloBlocks
         * @description Блоки хаба соло-режимов.
         */
        SoloBlocks: {
            /** @description Наборы дня */
            daily: components["schemas"]["Block_list_DailyCard__"];
            /** @description Витрина: неплановые режимы домена по группам (source_type, presentation) */
            modes: components["schemas"]["Block_list_ModeGroup__"];
            /** @description Случайные режимы (витрина этапа 2) */
            random: components["schemas"]["Block_list_ModeCard__"];
            /** @description Личные рекорды; null — гость или аноним */
            records?: components["schemas"]["Block_list_RecordCard__"] | null;
            /** @description Наборы недели (сетка) */
            weekly: components["schemas"]["Block_list_DailyCard__"];
        };
        /**
         * SoloPage
         * @description ``GET /v1/pages/solo``.
         */
        SoloPage: {
            blocks: components["schemas"]["SoloBlocks"];
        };
        /**
         * SpinCard
         * @description Сегодняшний результат спина (если крутили).
         */
        SpinCard: {
            /**
             * Day
             * Format: date
             */
            day: string;
            prize: components["schemas"]["PrizeCard"];
            /** Repeated */
            repeated: boolean;
            /** Sector Key */
            sector_key: string;
            /**
             * Spun At
             * Format: date-time
             */
            spun_at: string;
        };
        /**
         * StreakCard
         * @description Серия дейлика по домену.
         */
        StreakCard: {
            /** Best */
            best: number;
            /** Current */
            current: number;
            /**
             * Domain
             * @enum {string}
             */
            domain: "music" | "film";
        };
        /**
         * Subject
         * @description Кто смотрит страницу.
         */
        Subject: {
            /** Display Name */
            display_name?: string | null;
            /**
             * Kind
             * @enum {string}
             */
            kind: "user" | "guest";
        };
        /**
         * SubjectPickCard
         * @description Выбор персон перед стартом (марафон): ``GET game /v1/modes/{key}/subjects``.
         */
        SubjectPickCard: {
            /** Max */
            max: number;
            /** Roles */
            roles: string[];
        };
        /**
         * ThemeCard
         * @description Активная тема оформления (ADR 0049): только ключ, наборы токенов лежат во фронтенде.
         */
        ThemeCard: {
            /** Key */
            key: string;
        };
        /**
         * TierCard
         * @description Звание ladder-а (ключи i18n из конфига rating).
         */
        TierCard: {
            /** Family */
            family: string;
            /** Key */
            key: string;
            /**
             * Step
             * @description Градация внутри звания; null — без градации
             */
            step: number | null;
            /** Title Key */
            title_key: string;
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
        /**
         * WalletCard
         * @description Версы игрока (progression ``/v1/me/wallet``).
         */
        WalletCard: {
            /** Earned */
            earned: number;
            /** Paid */
            paid: number;
            /** Total */
            total: number;
        };
        /**
         * WeeklyEntryCard
         * @description Строка топа недели.
         */
        WeeklyEntryCard: {
            /** Delta */
            delta: number;
            /** Nickname */
            nickname: string;
            /** Place */
            place: number;
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
        };
        /**
         * WeeklyTopCard
         * @description Топ недели ladder-а (прирост с понедельника, ADR 0039).
         */
        WeeklyTopCard: {
            /** Items */
            items: components["schemas"]["WeeklyEntryCard"][];
            /** Ladder */
            ladder: string;
            me?: components["schemas"]["WeeklyEntryCard"] | null;
            /**
             * Week Start
             * Format: date
             */
            week_start: string;
        };
        /**
         * WheelCard
         * @description Состояние колеса на сегодня (progression ``/v1/wheel``).
         */
        WheelCard: {
            /** Can Spin */
            can_spin: boolean;
            /** Enabled */
            enabled: boolean;
            /**
             * Next Spin At
             * @description null — можно крутить
             */
            next_spin_at: string | null;
            /** Sectors */
            sectors: components["schemas"]["WheelSectorCard"][];
            /** @description null — сегодня не крутили */
            today?: components["schemas"]["SpinCard"] | null;
        };
        /**
         * WheelSectorCard
         * @description Сектор колеса: название, шанс и приз — из ответа progression.
         */
        WheelSectorCard: {
            /** Chance */
            chance: number;
            /** Key */
            key: string;
            prize: components["schemas"]["PrizeCard"];
            /** Title */
            title: string;
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
    collections_page_v1_pages_collections_get: {
        parameters: {
            query?: {
                curated?: boolean | null;
                cursor?: string | null;
                difficulty?: ("easy" | "medium" | "hard") | null;
                domain?: ("music" | "film") | null;
                mode?: string | null;
                q?: string | null;
                sort?: "featured" | "popular" | "new" | "likes" | "difficulty";
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
                    "application/json": components["schemas"]["CollectionsPage"];
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
        };
    };
    collection_page_v1_pages_collections__collection_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                collection_id: string;
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
                    "application/json": components["schemas"]["CollectionPage"];
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
        };
    };
    event_page_v1_pages_events__event_key__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Ключ события */
                event_key: string;
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
                    "application/json": components["schemas"]["EventPageView"];
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
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
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
    home_page_v1_pages_home_get: {
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
                    "application/json": components["schemas"]["HomePage"];
                };
            };
        };
    };
    leaderboards_page_v1_pages_leaderboards_get: {
        parameters: {
            query?: {
                around?: "me" | null;
                cursor?: string | null;
                ladder?: string | null;
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
                    "application/json": components["schemas"]["LeaderboardsPage"];
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
        };
    };
    library_page_v1_pages_library_get: {
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
                    "application/json": components["schemas"]["LibraryPage"];
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
    library_shelf_page_v1_pages_library__shelf_ref__get: {
        parameters: {
            query?: {
                cursor?: string | null;
            };
            header?: never;
            path: {
                shelf_ref: string;
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
                    "application/json": components["schemas"]["LibraryShelfPage"];
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
        };
    };
    match_page_v1_pages_match__match_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
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
                    "application/json": components["schemas"]["MatchPage"];
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
        };
    };
    online_page_v1_pages_online_get: {
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
                    "application/json": components["schemas"]["OnlinePage"];
                };
            };
        };
    };
    player_page_v1_pages_players__user_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                user_id: string;
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
                    "application/json": components["schemas"]["PlayerPage"];
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
        };
    };
    profile_page_v1_pages_profile_get: {
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
                    "application/json": components["schemas"]["ProfilePage"];
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
    results_page_v1_pages_results__session_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                session_id: string;
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
                    "application/json": components["schemas"]["ResultsPage"];
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
        };
    };
    shell_page_v1_pages_shell_get: {
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
                    "application/json": components["schemas"]["ShellPage"];
                };
            };
        };
    };
    shop_page_v1_pages_shop_get: {
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
                    "application/json": components["schemas"]["ShopPage"];
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
    solo_page_v1_pages_solo_get: {
        parameters: {
            query?: {
                domain?: ("music" | "film") | null;
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
                    "application/json": components["schemas"]["SoloPage"];
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
        };
    };
    session_v1_session_get: {
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
                    "application/json": components["schemas"]["SessionInfo"];
                };
            };
        };
    };
}
