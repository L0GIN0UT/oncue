/* eslint-disable */
// Сгенерировано `pnpm gen:api` из contracts/openapi/rating.json. Не править руками.

export interface paths {
    "/admin/v1/ladders": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Ladder-ы
         * @description Все ladder-ы с действующей и черновой версиями.
         */
        get: operations["ladders_admin_v1_ladders_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/ladders/{ladder}/draft": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Сохранить черновик
         * @description Валидация по `contracts/rating/ladder.v1.json` и инвариантам домена (звания по возрастанию, `below` растёт, ключ в теле равен ключу в пути): 422 `rating.ladder_invalid` с путями ошибок.
         */
        put: operations["save_draft_admin_v1_ladders__ladder__draft_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/ladders/{ladder}/leaderboard/rebuild": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Пересобрать лидерборд
         * @description Ставит запрос на пересборку лидерборда текущего сезона; выполняет задача `rating.leaderboards.rebuild`. Ошибки: 404 `rating.ladder_not_found`, 404 `rating.season_not_found`.
         */
        post: operations["rebuild_leaderboard_admin_v1_ladders__ladder__leaderboard_rebuild_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/ladders/{ladder}/publish": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Опубликовать версию
         * @description Версия становится действующей для новых матчей; уходит `LadderPublished`. Ошибки: 404 `rating.version_not_found`, 409 `rating.version_not_draft`.
         */
        post: operations["publish_admin_v1_ladders__ladder__publish_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/ladders/{ladder}/rollback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Откатить ladder
         * @description Копия ранее опубликованной версии публикуется новой версией (история не переписывается). Ошибки: 404 `rating.version_not_found`, 409 `rating.version_not_published` (версия никогда не публиковалась).
         */
        post: operations["rollback_admin_v1_ladders__ladder__rollback_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/ladders/{ladder}/seasons": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Создать сезон
         * @description Длина — из конфига ladder-а. Ошибки: 409 `rating.season_overlap` (пересечение с существующим сезоном).
         */
        post: operations["create_season_admin_v1_ladders__ladder__seasons_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/ladders/{ladder}/versions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Версии ladder-а
         * @description Черновик, действующая и архивные версии, новые первыми.
         */
        get: operations["versions_admin_v1_ladders__ladder__versions_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/ladders/{ladder}/versions/{version}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Версия ladder-а с конфигом
         * @description Тело конфига по `contracts/rating/ladder.v1.json` (для diff в админке).
         */
        get: operations["version_admin_v1_ladders__ladder__versions__version__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/seasons/{season_id}/close": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Закрыть сезон
         * @description Мягкий сброс по конфигу, `SeasonFinished`, следующий сезон открывается автоматически. Ошибки: 404 `rating.season_not_found`, 409 `rating.season_not_active`.
         */
        post: operations["close_season_admin_v1_seasons__season_id__close_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/ladders": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Ladder-ы
         * @description Опубликованные ladder-ы с действующим сезоном, званиями и длиной калибровки (всё из конфига ladder-а). Публично, кэшируется.
         */
        get: operations["ladders_v1_ladders_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/ladders/{ladder}/leaderboard": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Лидерборд ladder-а
         * @description Сезонный лидерборд: курсор или `around=me` (окно вокруг пользователя, нужен токен — иначе 401; в калибровке — 404 `rating.not_ranked`). `season` — прошлый сезон по `SeasonFinished.standings_ref`; без него — текущий. `limit` по умолчанию — `leaderboard.page_size` конфига. Ошибки: 404 `rating.ladder_not_found`, 404 `rating.season_not_found`. Публично, кэш 10 с.
         */
        get: operations["leaderboard_v1_ladders__ladder__leaderboard_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/ladders/{ladder}/weekly": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Топ недели
         * @description Прирост рейтинга с понедельника (зона и размер — из конфига ladder-а, ADR 0039). Ошибки: 404 `rating.ladder_not_found`. Публично, кэш 10 с.
         */
        get: operations["weekly_v1_ladders__ladder__weekly_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/matches/{match_id}/changes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Дельты матча
         * @description Изменения рейтинга игроков матча для экрана итога; только участник. Пока событие `MatchFinished` не обработано — 404 `rating.match_not_rated` (клиент опрашивает до 10 с); не рейтинговый матч и чужой матч — тоже 404. В калибровке числа `null`.
         */
        get: operations["match_changes_v1_matches__match_id__changes_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/me/ratings": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Мои рейтинги
         * @description Рейтинг, звание и калибровка по каждому ladder-у в текущем сезоне.
         */
        get: operations["my_ratings_v1_me_ratings_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/me/ratings/{ladder}/history": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * История рейтинга
         * @description Изменения рейтинга за матчи, новые первыми (курсор).
         */
        get: operations["history_v1_me_ratings__ladder__history_get"];
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
    "/v1/users/{user_id}/ratings": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Рейтинги пользователя
         * @description Публичный профиль: звания и места; в калибровке числа скрыты. Ошибки: 404 `rating.user_not_found` (нет или удалён).
         */
        get: operations["user_ratings_v1_users__user_id__ratings_get"];
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
         * AdminLadderCard
         * @description Ladder в админке: действующая и черновая версии.
         */
        AdminLadderCard: {
            /** Draft Version */
            draft_version: number | null;
            /** Key */
            key: string;
            /**
             * Published Version
             * @description null — ещё не публиковался
             */
            published_version: number | null;
            /**
             * Season Id
             * @description Текущий сезон; null — между сезонами
             */
            season_id: string | null;
        };
        /**
         * AdminLadderList
         * @description Ladder-ы админки.
         */
        AdminLadderList: {
            /** Items */
            items: components["schemas"]["AdminLadderCard"][];
        };
        /**
         * CloseSeasonRequest
         * @description Закрыть сезон досрочно: мягкий сброс, `SeasonFinished`, следующий сезон открывается.
         */
        CloseSeasonRequest: {
            /** Reason */
            reason: string;
        };
        /**
         * CreateSeasonRequest
         * @description Создать сезон: по умолчанию — следующий сразу после текущего, длина — из конфига.
         */
        CreateSeasonRequest: {
            /** Reason */
            reason: string;
            /**
             * Starts At
             * @description С часовым поясом; окно дат — настройки сервиса. null — после текущего сезона
             */
            starts_at?: string | null;
        };
        /**
         * HistoryItem
         * @description Изменение рейтинга за матч.
         */
        HistoryItem: {
            /**
             * At
             * Format: date-time
             */
            at: string;
            /** Delta */
            delta: number | null;
            /** Format */
            format: string;
            /**
             * Match Id
             * Format: uuid
             */
            match_id: string;
            /** New */
            new: number | null;
            /**
             * Old
             * @description null в калибровке: числа скрыты
             */
            old: number | null;
            /**
             * Outcome
             * @enum {string}
             */
            outcome: "win" | "loss" | "draw";
            tier: components["schemas"]["TierView"] | null;
        };
        /**
         * HistoryPage
         * @description История рейтинга пользователя, новые первыми.
         */
        HistoryPage: {
            /** Items */
            items: components["schemas"]["HistoryItem"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        JsonValue: unknown;
        /**
         * LadderList
         * @description Опубликованные ladder-ы.
         */
        LadderList: {
            /** Items */
            items: components["schemas"]["LadderView"][];
        };
        /**
         * LadderVersionBody
         * @description Версия ladder-а с телом конфига.
         */
        LadderVersionBody: {
            card: components["schemas"]["LadderVersionCard"];
            /**
             * Config
             * @description По `contracts/rating/ladder.v1.json`
             */
            config: {
                [key: string]: components["schemas"]["JsonValue"];
            };
        };
        /**
         * LadderVersionCard
         * @description Версия конфига ladder-а.
         */
        LadderVersionCard: {
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Created By
             * Format: uuid
             */
            created_by: string;
            /** Published At */
            published_at?: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "draft" | "published" | "archived";
            /** Version */
            version: number;
        };
        /**
         * LadderVersionList
         * @description Версии ladder-а, новые первыми.
         */
        LadderVersionList: {
            /** Items */
            items: components["schemas"]["LadderVersionCard"][];
        };
        /**
         * LadderView
         * @description Ladder (в 4b — по одному на домен) с действующим сезоном.
         */
        LadderView: {
            /**
             * Calibration Matches
             * @description Длина калибровки, матчей
             */
            calibration_matches: number;
            /**
             * Domain
             * @enum {string}
             */
            domain: "music" | "film";
            /** Formats */
            formats: string[];
            /** Key */
            key: string;
            /** @description null — между сезонами */
            season: components["schemas"]["SeasonView"] | null;
            /** Tiers */
            tiers: components["schemas"]["TierView"][];
            /**
             * Version
             * @description Версия опубликованного конфига
             */
            version: number;
        };
        /**
         * LeaderboardEntry
         * @description Строка лидерборда (ник — из проекции профиля, ПДн не отдаются).
         */
        LeaderboardEntry: {
            /** Matches Played */
            matches_played: number;
            /** Nickname */
            nickname: string;
            /** Place */
            place: number;
            /** Rating */
            rating: number;
            tier: components["schemas"]["TierView"];
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
        };
        /**
         * LeaderboardPage
         * @description Страница лидерборда: keyset-курсор или окно вокруг пользователя (``around=me``).
         */
        LeaderboardPage: {
            /** Items */
            items: components["schemas"]["LeaderboardEntry"][];
            /** Ladder */
            ladder: string;
            /** @description Строка пользователя; только с токеном и вне калибровки */
            me?: components["schemas"]["LeaderboardEntry"] | null;
            /** Next Cursor */
            next_cursor?: string | null;
            /**
             * Season Id
             * Format: uuid
             */
            season_id: string;
        };
        /**
         * MatchChange
         * @description Дельта одного игрока за матч (для итога, ``GET /v1/matches/{id}/changes``).
         */
        MatchChange: {
            /** Calibrating */
            calibrating: boolean;
            /** Calibration Left */
            calibration_left: number;
            /** Delta */
            delta: number | null;
            /** Format */
            format: string;
            /** Ladder */
            ladder: string;
            /**
             * Leaver Penalty
             * @description Штраф за выход или сдачу, входит в delta
             */
            leaver_penalty: number;
            /** New */
            new: number | null;
            /**
             * Old
             * @description null в калибровке
             */
            old: number | null;
            old_tier: components["schemas"]["TierView"] | null;
            /**
             * Outcome
             * @enum {string}
             */
            outcome: "win" | "loss" | "draw";
            tier: components["schemas"]["TierView"] | null;
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
        };
        /**
         * MatchChanges
         * @description Дельты матча: итог опрашивает ручку до 10 с, пока консьюмер не применит событие.
         */
        MatchChanges: {
            /** Items */
            items: components["schemas"]["MatchChange"][];
            /**
             * Match Id
             * Format: uuid
             */
            match_id: string;
        };
        /**
         * MyRatings
         * @description Рейтинги пользователя по ladder-ам (``GET /v1/me/ratings``, ``/users/{id}/ratings``).
         */
        MyRatings: {
            /** Items */
            items: components["schemas"]["RatingView"][];
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
         * PublishRequest
         * @description Опубликовать версию (черновик становится действующим, уходит `LadderPublished`).
         */
        PublishRequest: {
            /** Reason */
            reason: string;
            /** Version */
            version: number;
        };
        /**
         * RatingView
         * @description Рейтинг игрока в ladder-е в текущем сезоне.
         */
        RatingView: {
            /** Calibrating */
            calibrating: boolean;
            /**
             * Calibration Left
             * @description Сколько матчей осталось до звания
             */
            calibration_left: number;
            /** Ladder */
            ladder: string;
            /** Matches Played */
            matches_played: number;
            /**
             * Next Tier At
             * @description Рейтинг следующего звания; null — высшее или калибровка
             */
            next_tier_at?: number | null;
            /**
             * Place
             * @description Место в лидерборде; null — вне его
             */
            place?: number | null;
            /**
             * Rating
             * @description null, пока идёт калибровка (число скрыто)
             */
            rating: number | null;
            /**
             * Season Id
             * Format: uuid
             */
            season_id: string;
            /** @description null, пока идёт калибровка */
            tier: components["schemas"]["TierView"] | null;
        };
        /**
         * RebuildAccepted
         * @description Пересборка принята (выполняет задача ``rating.leaderboards.rebuild``).
         */
        RebuildAccepted: {
            /** Ladder */
            ladder: string;
            /**
             * Season Id
             * Format: uuid
             */
            season_id: string;
            /**
             * Status
             * @default accepted
             * @constant
             */
            status: "accepted";
        };
        /**
         * RebuildRequest
         * @description Пересобрать лидерборд сезона из Postgres.
         */
        RebuildRequest: {
            /** Reason */
            reason: string;
        };
        /**
         * RollbackRequest
         * @description Откатить ladder к ранее опубликованной версии (публикуется новой версией-копией).
         */
        RollbackRequest: {
            /** Reason */
            reason: string;
            /** To Version */
            to_version: number;
        };
        /**
         * SaveDraftRequest
         * @description Сохранить черновик конфига ladder-а.
         */
        SaveDraftRequest: {
            /**
             * Config
             * @description По `contracts/rating/ladder.v1.json`; ключ в теле равен ключу в пути
             */
            config: {
                [key: string]: components["schemas"]["JsonValue"];
            };
            /** Reason */
            reason: string;
        };
        /**
         * SeasonCard
         * @description Сезон ladder-а.
         */
        SeasonCard: {
            /**
             * Ends At
             * Format: date-time
             */
            ends_at: string;
            /** Finished At */
            finished_at?: string | null;
            /** Ladder */
            ladder: string;
            /** Number */
            number: number;
            /**
             * Season Id
             * Format: uuid
             */
            season_id: string;
            /**
             * Starts At
             * Format: date-time
             */
            starts_at: string;
            /**
             * Status
             * @enum {string}
             */
            status: "scheduled" | "active" | "finished";
        };
        /**
         * SeasonView
         * @description Сезон ladder-а.
         */
        SeasonView: {
            /**
             * Ends At
             * Format: date-time
             * @description «Осталось X д.» считает клиент по этому полю
             */
            ends_at: string;
            /** Number */
            number: number;
            /**
             * Season Id
             * Format: uuid
             */
            season_id: string;
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
         * TierView
         * @description Звание ladder-а: семейство и градация внутри него.
         */
        TierView: {
            /**
             * Below
             * @description Звание действует при рейтинге строго ниже; null — высшее
             */
            below: number | null;
            /**
             * Family
             * @description Семейство звания; набор семейств задаёт конфиг ladder-а
             */
            family: string;
            /**
             * Key
             * @description Ключ из конфига ladder-а, например `silver_2`
             */
            key: string;
            /**
             * Step
             * @description Градация (1, 2, 3); null — без градации
             */
            step: number | null;
            /**
             * Title Key
             * @description Ключ i18n названия
             */
            title_key: string;
        };
        /**
         * WeeklyEntry
         * @description Строка топа недели.
         */
        WeeklyEntry: {
            /**
             * Delta
             * @description Прирост рейтинга с понедельника
             */
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
         * WeeklyTop
         * @description Топ недели ladder-а (прирост с понедельника в зоне конфига, ADR 0039).
         */
        WeeklyTop: {
            /** Items */
            items: components["schemas"]["WeeklyEntry"][];
            /** Ladder */
            ladder: string;
            me?: components["schemas"]["WeeklyEntry"] | null;
            /**
             * Season Id
             * Format: uuid
             */
            season_id: string;
            /**
             * Week Start
             * Format: date
             */
            week_start: string;
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
    ladders_admin_v1_ladders_get: {
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
                    "application/json": components["schemas"]["AdminLadderList"];
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
    save_draft_admin_v1_ladders__ladder__draft_put: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description Ключ ladder-а */
                ladder: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SaveDraftRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LadderVersionBody"];
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
    rebuild_leaderboard_admin_v1_ladders__ladder__leaderboard_rebuild_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description Ключ ladder-а */
                ladder: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RebuildRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RebuildAccepted"];
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
    publish_admin_v1_ladders__ladder__publish_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description Ключ ladder-а */
                ladder: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PublishRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LadderVersionBody"];
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
    rollback_admin_v1_ladders__ladder__rollback_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description Ключ ladder-а */
                ladder: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RollbackRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LadderVersionBody"];
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
    create_season_admin_v1_ladders__ladder__seasons_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description Ключ ladder-а */
                ladder: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreateSeasonRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SeasonCard"];
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
    versions_admin_v1_ladders__ladder__versions_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Ключ ladder-а */
                ladder: string;
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
                    "application/json": components["schemas"]["LadderVersionList"];
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
    version_admin_v1_ladders__ladder__versions__version__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Ключ ladder-а */
                ladder: string;
                version: number;
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
                    "application/json": components["schemas"]["LadderVersionBody"];
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
    close_season_admin_v1_seasons__season_id__close_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                season_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CloseSeasonRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SeasonCard"];
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
    ladders_v1_ladders_get: {
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
                    "application/json": components["schemas"]["LadderList"];
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
        };
    };
    leaderboard_v1_ladders__ladder__leaderboard_get: {
        parameters: {
            query?: {
                around?: "me" | null;
                cursor?: string | null;
                limit?: number | null;
                season?: string | null;
            };
            header?: never;
            path: {
                /** @description Ключ ladder-а (`music`, `film`) */
                ladder: string;
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
                    "application/json": components["schemas"]["LeaderboardPage"];
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
    weekly_v1_ladders__ladder__weekly_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Ключ ladder-а (`music`, `film`) */
                ladder: string;
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
                    "application/json": components["schemas"]["WeeklyTop"];
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
    match_changes_v1_matches__match_id__changes_get: {
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
                    "application/json": components["schemas"]["MatchChanges"];
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
    my_ratings_v1_me_ratings_get: {
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
                    "application/json": components["schemas"]["MyRatings"];
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
    history_v1_me_ratings__ladder__history_get: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
            };
            header?: never;
            path: {
                /** @description Ключ ladder-а (`music`, `film`) */
                ladder: string;
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
                    "application/json": components["schemas"]["HistoryPage"];
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
    user_ratings_v1_users__user_id__ratings_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Идентификатор пользователя */
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
                    "application/json": components["schemas"]["MyRatings"];
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
}
