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
        /** Block[ResultSummary] */
        Block_ResultSummary_: {
            data?: components["schemas"]["ResultSummary"] | null;
            /**
             * Status
             * @enum {string}
             */
            status: "ok" | "unavailable";
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
         * HomeBlocks
         * @description Блоки главной.
         */
        HomeBlocks: {
            /** @description Наборы дня (без недельной сетки) */
            dailies: components["schemas"]["Block_list_DailyCard__"];
            modes: components["schemas"]["Block_list_ModeCard__"];
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
