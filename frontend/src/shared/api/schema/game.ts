/* eslint-disable */
// Сгенерировано `pnpm gen:api` из contracts/openapi/game.json. Не править руками.

export interface paths {
    "/admin/v1/daily-sets": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Наборы дня за период */
        get: operations["list_daily_sets_admin_v1_daily_sets_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/daily-sets/{mode_key}/{day}/items/{index}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Заменить единицу набора
         * @description 409 `game.daily_locked` (день начался), `game.item_not_playable`, `game.item_duplicate`.
         */
        put: operations["replace_daily_item_admin_v1_daily_sets__mode_key___day__items__index__put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/daily-sets/{mode_key}/{day}/regenerate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Перегенерировать набор */
        post: operations["regenerate_daily_set_admin_v1_daily_sets__mode_key___day__regenerate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/mode-configs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Режимы (конфиги) */
        get: operations["list_mode_configs_admin_v1_mode_configs_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/mode-configs/{mode_key}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Режим: версии и черновик */
        get: operations["get_mode_config_admin_v1_mode_configs__mode_key__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/mode-configs/{mode_key}/archive": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Архивировать режим */
        post: operations["archive_mode_config_admin_v1_mode_configs__mode_key__archive_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/mode-configs/{mode_key}/asset-requests": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Запросы ассетов режима */
        get: operations["list_asset_requests_admin_v1_mode_configs__mode_key__asset_requests_get"];
        put?: never;
        /**
         * Запросить ассеты под загадку черновика
         * @description Если под `params_hash(clue.params)` (и `clue.reveal.params`) черновика в проекции нет ассетов — событие `game.ClueAssetsRequested` (ADR 0025). Повтор с тем же ключом — тот же ответ; пока запрос `pending`, повтор тех же параметров отвечает им же без нового события. 409 `game.assets_already_available` — ассеты уже есть; 422 `game.config_invalid` — параметры не проходят схему загадки.
         */
        post: operations["request_assets_admin_v1_mode_configs__mode_key__asset_requests_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/mode-configs/{mode_key}/draft": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Сохранить черновик
         * @description Сохраняет и невалидный черновик; итог проверки — в `validation`. 409 `game.draft_conflict` — `base_revision` устарела.
         */
        put: operations["save_draft_admin_v1_mode_configs__mode_key__draft_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/mode-configs/{mode_key}/publish": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Опубликовать черновик
         * @description Только валидный черновик ревизии `draft_revision` → новая неизменяемая версия, событие `game.ModeConfigPublished`. 422 `game.config_invalid`, 409 `game.draft_conflict`.
         */
        post: operations["publish_mode_config_admin_v1_mode_configs__mode_key__publish_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/mode-configs/{mode_key}/rollback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Откатить к версии */
        post: operations["rollback_mode_config_admin_v1_mode_configs__mode_key__rollback_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/mode-configs/{mode_key}/subjects": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Темы периода сетки: поиск персон
         * @description Хвост 3a (план этапа 3b §2.5): админ выбирает тему недели поиском, а не UUID. Роли и `min_items` — из конфига сетки (`source.params.subject`); по убыванию числа играбельных единиц. Режим без темы-персоны — 422 `game.subjects_not_supported`.
         */
        get: operations["admin_subjects_admin_v1_mode_configs__mode_key__subjects_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/mode-configs/{mode_key}/versions/{version}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Версия конфига */
        get: operations["get_mode_config_version_admin_v1_mode_configs__mode_key__versions__version__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/mode-configs/schema": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Сводная JSON Schema конфига (подсказки редактора) */
        get: operations["mode_config_schema_admin_v1_mode_configs_schema_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/mode-configs/validate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Проверить тело конфига
         * @description Без сохранения; ошибки с JSON-путями и размер пула по проекции.
         */
        post: operations["validate_mode_config_admin_v1_mode_configs_validate_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/projection/items": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Поиск кандидатов в проекции game */
        get: operations["search_projection_admin_v1_projection_items_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/collections/{collection_id}/modes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Режимы сборника
         * @description Опубликованные режимы с source `collection` домена сборника и сколько единиц в каждом играбельно. Только опубликованный, не скрытый сборник (по проекции `collections.collection.v1`), иначе 404 `game.collection_not_found`. Без названий и `media_item_id` (D6). Субъект не обязателен.
         */
        get: operations["collection_modes_v1_collections__collection_id__modes_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/daily": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Дейлики текущего субъекта */
        get: operations["daily_status_v1_daily_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/me/records": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Личные рекорды
         * @description Только для пользователей; гостю — пустой список.
         */
        get: operations["my_records_v1_me_records_get"];
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
    "/v1/modes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Опубликованные режимы
         * @description Публичный, `Cache-Control: public, max-age=30`.
         */
        get: operations["list_modes_v1_modes_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/modes/{mode_key}/playability": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Играбельность единиц черновика сборника
         * @description Для конструктора: какие из ≤ 200 единиц играбельны в режиме (активны в каталоге и есть каталожный набор ассетов). Только пользователи (гость — 403 `auth.guest_forbidden`), лимит `GV_GAME_LIMITS__PLAYABILITY_PER_MIN` (429). Режим без source `collection` — 422 `game.collection_not_supported`.
         */
        post: operations["playability_v1_modes__mode_key__playability_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/modes/{mode_key}/subjects": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Персоны для марафона
         * @description Поиск по имени среди персон с ролью режима и не меньше `min_items` играбельных единиц; по убыванию числа единиц. 422 `game.subjects_not_supported` — режим не выбирает персон; 429.
         */
        get: operations["list_subjects_v1_modes__mode_key__subjects_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/sessions": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Начать сессию
         * @description 201 — новая сессия. Для дейлика при незавершённой сессии дня — 200 с ней же. Ошибки: 409 `game.daily_already_played` (`details.session_id`), 403 `game.guest_not_allowed`, 404 `game.mode_not_found`, 503 `game.daily_not_ready` (набор периода не готов), 503 `game.pool_empty`. Марафон: `subject_ids` обязательны — 422 `game.subject_required`, `game.too_many_subjects`; режим без выбора персон — 422 `game.subjects_not_supported`. Сетка периода: повторный вход в доигранную — 409 `game.daily_already_played`. Сборник (`collection_id`, `rounds`): 404 `game.collection_not_found` (нет, скрыт или удалён), 422 `game.collection_required` (режим с source `collection` без сборника), 422 `game.collection_domain_mismatch`, 422 `game.rounds_not_allowed` (нет в `round_options`), 422 `game.collection_not_supported` (`collection_id` у режима без source `collection`), 422 `game.pool_too_small`.
         */
        post: operations["start_session_v1_sessions_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/sessions/{session_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Снимок сессии */
        get: operations["get_session_v1_sessions__session_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/sessions/{session_id}/guesses": {
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
         * @description Ответ — `{media_item_id}`, `{text}` (формат `classic`) или `{option_id}` (формат `pick`). Ошибки: 409 `game.desync` (`details.session` — актуальный `SessionSnapshot`), 409 `game.round_finished`, 422 `game.answer_kind_unsupported` (вид ответа не для формата режима), 422 `game.option_unknown`, 429. У сетки — `stats.agree_pct`.
         */
        post: operations["submit_guess_v1_sessions__session_id__guesses_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/sessions/{session_id}/result": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Итог завершённой сессии */
        get: operations["get_result_v1_sessions__session_id__result_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/sessions/{session_id}/rounds/{index}/clue": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Ссылка на ассет уровня
         * @description POST: каждая выдача пишется в журнал media-pipeline и ограничена лимитом. Ошибки: 403 `game.level_locked`, 410 `game.asset_unavailable` (раунд становится `void`), 429, 503 `game.assets_unavailable`.
         */
        post: operations["issue_clue_v1_sessions__session_id__rounds__index__clue_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/sessions/{session_id}/rounds/{index}/final-clue": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Свежая ссылка на ассет ревила
         * @description Только для завершённого не-void раунда своей сессии; ограничена лимитом. Ошибки: 409 `game.round_not_finished`, 410 `game.asset_unavailable`, 429.
         */
        post: operations["final_clue_v1_sessions__session_id__rounds__index__final_clue_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/sessions/{session_id}/rounds/{index}/give-up": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Сдаться в раунде
         * @description Ошибки: 409 `game.desync` (`details.session` — актуальный `SessionSnapshot`), 409 `game.round_finished`, 429.
         */
        post: operations["give_up_v1_sessions__session_id__rounds__index__give_up_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/sessions/{session_id}/skips": {
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
         * @description Ошибки: 409 `game.desync` (`details.session` — актуальный `SessionSnapshot`), 409 `game.round_finished`, 429.
         */
        post: operations["skip_level_v1_sessions__session_id__skips_post"];
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
         * ArchiveRequest
         * @description Убрать режим из каталога (сессии доигрываются).
         */
        ArchiveRequest: {
            /** Reason */
            reason: string;
        };
        /**
         * AssetRequestCreate
         * @description Запросить генерацию ассетов под параметры загадки черновика (ADR 0025).
         */
        AssetRequestCreate: {
            /** Draft Revision */
            draft_revision: number;
            /** Reason */
            reason: string;
        };
        /**
         * AssetRequestOut
         * @description Запрос ассетов: статус профиля смотрится в media-pipeline (``/admin/v1/profiles``).
         */
        AssetRequestOut: {
            /** Actor Id */
            actor_id: string | null;
            /** Clue Type */
            clue_type: string;
            /** Draft Revision */
            draft_revision: number;
            /** Mode Key */
            mode_key: string;
            /** Params Hash */
            params_hash: string;
            /** Reason */
            reason: string;
            /**
             * Request Id
             * Format: uuid
             */
            request_id: string;
            /**
             * Requested At
             * Format: date-time
             */
            requested_at: string;
            /**
             * Status
             * @enum {string}
             */
            status: "pending" | "sent";
        };
        /**
         * BracketOut
         * @description Итог сетки: чемпион и выборы по турам.
         */
        BracketOut: {
            /** Champion */
            champion: string;
            /** Picks */
            picks: components["schemas"]["BracketPick"][];
        };
        /**
         * BracketPick
         * @description Выбор в сетке.
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
         * ClueMeta
         * @description Параметры уровня для UI — поля ``hint_fields`` плагина загадки ядра.
         *
         *     Набор полей — объединение ``hint_fields`` встроенных загадок; у конкретной загадки
         *     заполнены только её поля, остальные ``null``.
         */
        ClueMeta: {
            /**
             * Blur
             * @deprecated
             * @description Устарело, всегда null: размытие передаётся в blur_px
             */
            blur?: number | null;
            /**
             * Blur Px
             * @description frame_progressive: размытие, px
             */
            blur_px?: number | null;
            /**
             * Count
             * @description emoji: сколько эмодзи открыто (null у meta — все)
             */
            count?: number | null;
            /**
             * Crop
             * @description frame_progressive: видимая доля кадра (0..1]
             */
            crop?: number | null;
            /**
             * Fields
             * @description description: поля, открытые на уровне
             */
            fields?: string[] | null;
            /**
             * Frame
             * @description frame_sequence: позиция кадра
             */
            frame?: number | null;
            /**
             * Pixel Block
             * @description poster_masked: размер блока пикселизации, px
             */
            pixel_block?: number | null;
            /**
             * Seconds
             * @description audio_intro: длина отрывка, с
             */
            seconds?: number | null;
        };
        /**
         * ClueOut
         * @description Короткоживущая ссылка на ассет уровня (TTL 60 с).
         */
        ClueOut: {
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
         * ClueRequest
         * @description Запрос ассета уровня (уровень не выше открытого).
         */
        ClueRequest: {
            /** Level */
            level: number;
        };
        /**
         * CollectionModeOut
         * @description Режим сборника (``GET /v1/collections/{id}/modes``): сколько единиц играбельно.
         */
        CollectionModeOut: {
            /** Allow Guest */
            allow_guest: boolean;
            /**
             * Available
             * @description playable ≥ min_items режима
             */
            available: boolean;
            /** Clue Type */
            clue_type: string;
            /** Mode Key */
            mode_key: string;
            /**
             * Playable
             * @description Играбельных единиц сборника в этом режиме
             */
            playable: number;
            /**
             * Round Options
             * @description Варианты числа раундов, доступные при текущем playable
             */
            round_options: (5 | 10 | 25 | "all")[];
            /** Title Key */
            title_key: string;
            /**
             * Total
             * @description Единиц в сборнике (активных в каталоге)
             */
            total: number;
        };
        /**
         * CollectionRef
         * @description Сборник сессии: версия состава фиксируется при старте (ADR 0028).
         */
        CollectionRef: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Version
             * @description content_version сборника
             */
            version: number;
        };
        ConfigBody: {
            [key: string]: unknown;
        };
        /**
         * DailyRoundStat
         * @description Доля угадавших раунд дейлика (только пользователи).
         */
        DailyRoundStat: {
            /** Exact Pct */
            exact_pct: number;
            /** Index */
            index: number;
        };
        /**
         * DailySet
         * @description Набор периода режима (дейлик — день, сетка — неделя; ``day`` — начало периода).
         */
        DailySet: {
            /**
             * Day
             * Format: date
             */
            day: string;
            /** Items */
            items: components["schemas"]["DailySetItem"][];
            /** Mode Key */
            mode_key: string;
            /** Mode Version */
            mode_version: number;
            /**
             * Period
             * @default day
             * @enum {string}
             */
            period: "day" | "week";
            /**
             * Status
             * @enum {string}
             */
            status: "scheduled" | "locked" | "failed";
            /**
             * Subject Id
             * @description Тема периода (персона сетки)
             */
            subject_id?: string | null;
        };
        /**
         * DailySetItem
         * @description Единица набора дня.
         */
        DailySetItem: {
            /** Index */
            index: number;
            /**
             * Media Item Id
             * Format: uuid
             */
            media_item_id: string;
            /** Playable */
            playable: boolean;
            /** Title */
            title: string;
            /** Year */
            year: number | null;
        };
        /**
         * DailyStatus
         * @description Статус дейлика для текущего субъекта (гость или пользователь).
         */
        DailyStatus: {
            /**
             * Day
             * Format: date
             */
            day: string;
            /** Max Score */
            max_score: number;
            /** Mode Key */
            mode_key: string;
            /**
             * Next Reset At
             * Format: date-time
             */
            next_reset_at: string;
            /** Score */
            score?: number | null;
            /** Session Id */
            session_id?: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "not_started" | "in_progress" | "finished";
        };
        /**
         * DraftOut
         * @description Черновик (один на ключ); сохраняется и невалидным.
         */
        DraftOut: {
            body: components["schemas"]["ConfigBody"];
            /** Revision */
            revision: number;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Updated By */
            updated_by: string | null;
            validation: components["schemas"]["ValidationResult"];
        };
        /**
         * FinalClue
         * @description Полный ассет для ревила.
         */
        FinalClue: {
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
            /** Mime */
            mime: string;
            /** Url */
            url: string;
        };
        /**
         * GiveUpRequest
         * @description Сдаться в раунде.
         */
        GiveUpRequest: {
            /** Expected Version */
            expected_version: number;
        };
        /**
         * GuessOut
         * @description Ход в истории раунда.
         */
        GuessOut: {
            /** Display */
            display: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "item" | "text" | "option" | "skip";
            /**
             * Result
             * @description null у пропуска
             */
            result: ("exact" | "partial" | "miss") | null;
        };
        /**
         * GuessRequest
         * @description Ответ на текущем уровне раунда.
         */
        GuessRequest: {
            /** Answer */
            answer: components["schemas"]["ItemAnswer"] | components["schemas"]["TextAnswer"] | components["schemas"]["OptionAnswer"];
            /** Expected Version */
            expected_version: number;
            /** Round Index */
            round_index: number;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /**
         * ItemAnswer
         * @description Ответ выбором из автокомплита.
         */
        ItemAnswer: {
            /**
             * Media Item Id
             * Format: uuid
             */
            media_item_id: string;
        };
        /**
         * LevelOut
         * @description Уровень загадки для UI.
         */
        LevelOut: {
            /**
             * Hint
             * @description Что открывает уровень: {seconds} для аудио, {blur_px, crop} для кадра, {pixel_block} для постера, {frame} для последовательности кадров, {fields} для описания (накопленные поля), {count} для эмодзи (null — все)
             */
            hint: {
                [key: string]: number | string | string[] | null;
            };
            /** Index */
            index: number;
        };
        /**
         * ModeConfigDetail
         * @description Режим: версии и черновик.
         */
        ModeConfigDetail: {
            /**
             * Domain
             * @enum {string}
             */
            domain: "music" | "film";
            draft: components["schemas"]["DraftOut"] | null;
            /** Key */
            key: string;
            /** Published Version */
            published_version: number | null;
            /**
             * Status
             * @enum {string}
             */
            status: "draft" | "published" | "archived";
            /** Versions */
            versions: components["schemas"]["ModeConfigVersionSummary"][];
        };
        /**
         * ModeConfigSummary
         * @description Строка списка режимов.
         */
        ModeConfigSummary: {
            /**
             * Domain
             * @enum {string}
             */
            domain: "music" | "film";
            /** Has Draft */
            has_draft: boolean;
            /** Key */
            key: string;
            /** Published Version */
            published_version: number | null;
            /**
             * Status
             * @description draft — режим ещё ни разу не опубликован
             * @enum {string}
             */
            status: "draft" | "published" | "archived";
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /**
         * ModeConfigVersion
         * @description Версия с телом.
         */
        ModeConfigVersion: {
            /** Actor Id */
            actor_id: string | null;
            body: components["schemas"]["ConfigBody"];
            /** Body Hash */
            body_hash: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Key */
            key: string;
            /** Reason */
            reason: string | null;
            /** Rolled Back From */
            rolled_back_from?: number | null;
            /**
             * Status
             * @enum {string}
             */
            status: "published" | "archived";
            /** Version */
            version: number;
        };
        /**
         * ModeConfigVersionSummary
         * @description Опубликованная версия (неизменяемая).
         */
        ModeConfigVersionSummary: {
            /** Actor Id */
            actor_id: string | null;
            /** Body Hash */
            body_hash: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Reason */
            reason: string | null;
            /** Rolled Back From */
            rolled_back_from?: number | null;
            /**
             * Status
             * @enum {string}
             */
            status: "published" | "archived";
            /** Version */
            version: number;
        };
        /**
         * ModeOut
         * @description Опубликованный режим (``GET /v1/modes``).
         */
        ModeOut: {
            /** Allow Guest */
            allow_guest: boolean;
            /**
             * Answer Format
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
            /** Levels */
            levels: components["schemas"]["LevelOut"][];
            /**
             * Presentation
             * @default score
             * @enum {string}
             */
            presentation: "score" | "streak" | "bracket";
            /** Presets */
            presets?: components["schemas"]["PresetOut"][] | null;
            /** Rounds */
            rounds: number;
            /** Source Type */
            source_type: string;
            /** @description null — выбирать нечего */
            start?: components["schemas"]["ModeStart"] | null;
            /** Title Key */
            title_key: string;
            /** Version */
            version: number;
        };
        /**
         * ModeRef
         * @description Режим сессии (версия фиксируется при старте).
         */
        ModeRef: {
            /** Clue Type */
            clue_type: string;
            /**
             * Domain
             * @enum {string}
             */
            domain: "music" | "film";
            /** Key */
            key: string;
            /** Version */
            version: number;
        };
        /**
         * ModeStart
         * @description Что игрок выбирает перед стартом режима.
         */
        ModeStart: {
            subject?: components["schemas"]["SubjectPick"] | null;
        };
        /**
         * MoveResult
         * @description Итог хода.
         */
        MoveResult: {
            /**
             * Result
             * @description Результат ответа; null для пропуска и сдачи
             */
            result: ("exact" | "partial" | "miss") | null;
            round: components["schemas"]["RoundOut"];
            session: components["schemas"]["SessionSnapshot"];
            stats?: components["schemas"]["MoveStats"] | null;
        };
        /**
         * MoveStats
         * @description Статистика выбора (сетка): сколько игроков выбрали так же.
         */
        MoveStats: {
            /** Agree Pct */
            agree_pct: number;
        };
        /**
         * OptionAnswer
         * @description Ответ выбором варианта раунда (формат ``pick``).
         */
        OptionAnswer: {
            /** Option Id */
            option_id: string;
        };
        /**
         * OptionOut
         * @description Вариант ответа формата ``pick``.
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
         * PlayabilityOut
         * @description Какие из переданных единиц играбельны в режиме (без названий и ассетов, D6).
         */
        PlayabilityOut: {
            /** Mode Key */
            mode_key: string;
            /** Playable Ids */
            playable_ids: string[];
        };
        /**
         * PlayabilityRequest
         * @description Единицы черновика сборника для проверки играбельности в режиме.
         */
        PlayabilityRequest: {
            /** Media Item Ids */
            media_item_ids: string[];
        };
        /**
         * PoolInfo
         * @description Размер пула по проекции game.
         */
        PoolInfo: {
            /** Playable Count */
            playable_count: number;
        };
        /**
         * PresetOut
         * @description Пресет случайного режима.
         */
        PresetOut: {
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
         * ProjectionItem
         * @description Кандидат на замену из проекции game.
         */
        ProjectionItem: {
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
            /**
             * Playable
             * @description Есть ли ассеты под режим playable_for_mode; null — режим не задан
             */
            playable: boolean | null;
            /**
             * Status
             * @enum {string}
             */
            status: "active" | "blocked";
            /** Title */
            title: string;
            /** Year */
            year: number | null;
        };
        /**
         * PublishRequest
         * @description Опубликовать черновик ревизии ``draft_revision`` (только валидный).
         */
        PublishRequest: {
            /** Draft Revision */
            draft_revision: number;
            /** Reason */
            reason: string;
        };
        /**
         * RecordOut
         * @description Личный рекорд режима.
         */
        RecordOut: {
            /**
             * Achieved At
             * Format: date-time
             */
            achieved_at: string;
            /** Best */
            best: number;
            /** Mode Key */
            mode_key: string;
            /** Percentile */
            percentile?: number | null;
        };
        /**
         * RegenerateRequest
         * @description Перегенерировать набор (только ``scheduled`` / ``failed``); ``subject_id`` — новая тема
         *     периода (сетка), иначе тему выбирает генератор.
         */
        RegenerateRequest: {
            /** Reason */
            reason: string;
            /** Subject Id */
            subject_id?: string | null;
        };
        /**
         * ReplaceItemRequest
         * @description Заменить единицу набора (только ``scheduled``).
         */
        ReplaceItemRequest: {
            /**
             * Media Item Id
             * Format: uuid
             */
            media_item_id: string;
            /** Reason */
            reason: string;
        };
        /**
         * ResultRecord
         * @description Рекорд в итоге сессии (только пользователи).
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
         * ResultRound
         * @description Раунд в итоге сессии.
         */
        ResultRound: {
            /** Index */
            index: number;
            /** Level */
            level: number | null;
            /**
             * Outcome
             * @enum {string}
             */
            outcome: "exact" | "partial" | "miss" | "gave_up" | "void";
            /** Points */
            points: number;
            /** @description null у раунда void (ассет недоступен) */
            reveal: components["schemas"]["Reveal"] | null;
        };
        /**
         * Reveal
         * @description Ответ раунда — только у завершённого раунда.
         */
        Reveal: {
            final_clue: components["schemas"]["FinalClue"];
            /**
             * Media Item Id
             * Format: uuid
             */
            media_item_id: string;
            /**
             * Metric
             * @description Метрика сравнения загаданной единицы («больше/меньше»)
             */
            metric?: number | null;
            /** Persons */
            persons: components["schemas"]["PersonOut"][];
            /** Title */
            title: string;
            /** Year */
            year: number | null;
        };
        /**
         * RollbackRequest
         * @description Откат: копия версии ``to_version`` публикуется как новая версия.
         */
        RollbackRequest: {
            /** Reason */
            reason: string;
            /** To Version */
            to_version: number;
        };
        /**
         * RoundOut
         * @description Раунд в снимке сессии.
         */
        RoundOut: {
            /** Guesses */
            guesses: components["schemas"]["GuessOut"][];
            /** Index */
            index: number;
            /**
             * Level
             * @description Текущий открытый уровень
             */
            level: number;
            /** Levels Count */
            levels_count: number;
            /** Options */
            options?: components["schemas"]["OptionOut"][];
            /** Points */
            points?: number | null;
            progress?: components["schemas"]["RoundProgress"] | null;
            /** @description Только у завершённого раунда */
            reveal?: components["schemas"]["Reveal"] | null;
            /** Shown */
            shown?: components["schemas"]["ShownItem"][];
            /**
             * Status
             * @enum {string}
             */
            status: "pending" | "active" | "finished" | "void";
        };
        /**
         * RoundProgress
         * @description Прогресс сессии для UI: жизни, серия, тур сетки.
         */
        RoundProgress: {
            /** Lives Left */
            lives_left?: number | null;
            /**
             * Stage
             * @description Размер тура сетки: 32, 16 … 2
             */
            stage?: number | null;
            /** Streak */
            streak?: number | null;
        };
        /**
         * SaveDraftRequest
         * @description Сохранить черновик (оптимистичная блокировка по ``base_revision``).
         */
        SaveDraftRequest: {
            /**
             * Base Revision
             * @description Ревизия, от которой правили; null — черновика ещё нет
             */
            base_revision: number | null;
            body: components["schemas"]["ConfigBody"];
            /** Reason */
            reason: string;
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
         * SessionResult
         * @description Итог завершённой сессии.
         */
        SessionResult: {
            bracket?: components["schemas"]["BracketOut"] | null;
            /** @description Сессия по сборнику */
            collection?: components["schemas"]["CollectionRef"] | null;
            /** Daily Stats */
            daily_stats?: components["schemas"]["DailyRoundStat"][] | null;
            /** Max Score */
            max_score: number;
            /**
             * Presentation
             * @default score
             * @enum {string}
             */
            presentation: "score" | "streak" | "bracket";
            record?: components["schemas"]["ResultRecord"] | null;
            /** Rounds */
            rounds: components["schemas"]["ResultRound"][];
            /** Score */
            score: number;
            share: components["schemas"]["ShareOut"];
        };
        /**
         * SessionSnapshot
         * @description Снимок сессии; ``version`` — для ``expected_version`` следующего хода.
         */
        SessionSnapshot: {
            /** Current Round */
            current_round: number;
            /** Day */
            day?: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Max Score */
            max_score: number;
            mode: components["schemas"]["ModeRef"];
            /** Rounds */
            rounds: components["schemas"]["RoundOut"][];
            /** Score */
            score: number;
            /**
             * Status
             * @enum {string}
             */
            status: "active" | "finished" | "abandoned";
            /** Version */
            version: number;
        };
        /**
         * ShareOut
         * @description Текст-сетка для «Поделиться» (D14).
         */
        ShareOut: {
            /** Text */
            text: string;
        };
        /**
         * ShownItem
         * @description Единица, которую игрок видит открыто (опорная единица пары, вариант сетки).
         */
        ShownItem: {
            /** @description Кадр или отрывок варианта */
            clue?: components["schemas"]["FinalClue"] | null;
            /**
             * Metric
             * @description Только у опорной единицы пары
             */
            metric?: number | null;
            /** Option Id */
            option_id?: string | null;
            /** Title */
            title: string;
            /** Year */
            year?: number | null;
        };
        /**
         * SkipRequest
         * @description Пропуск уровня.
         */
        SkipRequest: {
            /** Expected Version */
            expected_version: number;
            /** Round Index */
            round_index: number;
        };
        /**
         * StartSessionRequest
         * @description Старт сессии режима.
         */
        StartSessionRequest: {
            /**
             * Collection Id
             * @description Сборник для режима с source `collection` (опубликованный, не скрытый)
             */
            collection_id?: string | null;
            /** Mode Key */
            mode_key: string;
            /** Preset Id */
            preset_id?: string | null;
            /**
             * Rounds
             * @description Число раундов из `round_options` режима сборника; null — первый вариант
             */
            rounds?: (5 | 10 | 25 | "all") | null;
            /**
             * Subject Ids
             * @description Персоны марафона
             */
            subject_ids?: string[] | null;
        };
        /**
         * SubjectOut
         * @description Персона для марафона (``GET /v1/modes/{key}/subjects``).
         */
        SubjectOut: {
            /**
             * Items
             * @description Сколько играбельных единиц у персоны
             */
            items: number;
            /** Name */
            name: string;
            /**
             * Role
             * @enum {string}
             */
            role: "artist" | "director" | "actor";
            /**
             * Subject Id
             * Format: uuid
             */
            subject_id: string;
        };
        /**
         * SubjectPick
         * @description Выбор персон игроком перед стартом (марафон).
         */
        SubjectPick: {
            /**
             * Max
             * @description Сколько персон можно выбрать
             */
            max: number;
            /** Roles */
            roles: ("artist" | "director" | "actor")[];
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
         * ValidateRequest
         * @description Проверить тело конфига без сохранения.
         */
        ValidateRequest: {
            body: components["schemas"]["ConfigBody"];
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
         * ValidationErrorItem
         * @description Ошибка конфига: путь поля, машинный код и ключ i18n текста.
         */
        ValidationErrorItem: {
            /** Code */
            code: string;
            /** Message Key */
            message_key: string;
            /** Path */
            path: string;
        };
        /**
         * ValidationResult
         * @description Итог проверки конфига.
         */
        ValidationResult: {
            /** Errors */
            errors: components["schemas"]["ValidationErrorItem"][];
            /** Ok */
            ok: boolean;
            /** @description null, если тело не разобрано */
            pool: components["schemas"]["PoolInfo"] | null;
            /** Warnings */
            warnings: components["schemas"]["ValidationWarning"][];
        };
        /**
         * ValidationWarning
         * @description Предупреждение (пул меньше нужного в bootstrap, ``assets.profile_missing``).
         */
        ValidationWarning: {
            /** Code */
            code: string;
            /**
             * Path
             * @description Секция конфига: clue.params | clue.reveal.params
             */
            path?: string | null;
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
    list_daily_sets_admin_v1_daily_sets_get: {
        parameters: {
            query: {
                from: string;
                mode_key: string;
                to: string;
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
                    "application/json": components["schemas"]["DailySet"][];
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
        };
    };
    replace_daily_item_admin_v1_daily_sets__mode_key___day__items__index__put: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                day: string;
                index: number;
                mode_key: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ReplaceItemRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DailySet"];
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
    regenerate_daily_set_admin_v1_daily_sets__mode_key___day__regenerate_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                day: string;
                mode_key: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RegenerateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["DailySet"];
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
    list_mode_configs_admin_v1_mode_configs_get: {
        parameters: {
            query?: {
                domain?: ("music" | "film") | null;
                status?: ("draft" | "published" | "archived") | null;
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
                    "application/json": components["schemas"]["ModeConfigSummary"][];
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
        };
    };
    get_mode_config_admin_v1_mode_configs__mode_key__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                mode_key: string;
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
                    "application/json": components["schemas"]["ModeConfigDetail"];
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
    archive_mode_config_admin_v1_mode_configs__mode_key__archive_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                mode_key: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ArchiveRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ModeConfigSummary"];
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
    list_asset_requests_admin_v1_mode_configs__mode_key__asset_requests_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                mode_key: string;
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
                    "application/json": components["schemas"]["AssetRequestOut"][];
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
    request_assets_admin_v1_mode_configs__mode_key__asset_requests_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                mode_key: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AssetRequestCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AssetRequestOut"];
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
    save_draft_admin_v1_mode_configs__mode_key__draft_put: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                mode_key: string;
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
                    "application/json": components["schemas"]["DraftOut"];
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
    publish_mode_config_admin_v1_mode_configs__mode_key__publish_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                mode_key: string;
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
                    "application/json": components["schemas"]["ModeConfigVersion"];
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
    rollback_mode_config_admin_v1_mode_configs__mode_key__rollback_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                mode_key: string;
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
                    "application/json": components["schemas"]["ModeConfigVersion"];
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
    admin_subjects_admin_v1_mode_configs__mode_key__subjects_get: {
        parameters: {
            query?: {
                limit?: number;
                q?: string | null;
            };
            header?: never;
            path: {
                mode_key: string;
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
                    "application/json": components["schemas"]["SubjectOut"][];
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
    get_mode_config_version_admin_v1_mode_configs__mode_key__versions__version__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                mode_key: string;
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
                    "application/json": components["schemas"]["ModeConfigVersion"];
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
    mode_config_schema_admin_v1_mode_configs_schema_get: {
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
                    "application/json": {
                        [key: string]: unknown;
                    };
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
        };
    };
    validate_mode_config_admin_v1_mode_configs_validate_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ValidateRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ValidationResult"];
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
        };
    };
    search_projection_admin_v1_projection_items_get: {
        parameters: {
            query?: {
                domain?: ("music" | "film") | null;
                limit?: number;
                playable_for_mode?: string | null;
                q?: string | null;
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
                    "application/json": components["schemas"]["ProjectionItem"][];
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
        };
    };
    collection_modes_v1_collections__collection_id__modes_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Идентификатор сборника */
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
                    "application/json": components["schemas"]["CollectionModeOut"][];
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
    daily_status_v1_daily_get: {
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
                    "application/json": components["schemas"]["DailyStatus"][];
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
    my_records_v1_me_records_get: {
        parameters: {
            query?: {
                mode_key?: string | null;
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
                    "application/json": components["schemas"]["RecordOut"][];
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
    list_modes_v1_modes_get: {
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
                    "application/json": components["schemas"]["ModeOut"][];
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
    playability_v1_modes__mode_key__playability_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Ключ режима */
                mode_key: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PlayabilityRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PlayabilityOut"];
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
    list_subjects_v1_modes__mode_key__subjects_get: {
        parameters: {
            query?: {
                limit?: number;
                q?: string | null;
            };
            header?: never;
            path: {
                /** @description Ключ режима */
                mode_key: string;
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
                    "application/json": components["schemas"]["SubjectOut"][];
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
    start_session_v1_sessions_post: {
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
                "application/json": components["schemas"]["StartSessionRequest"];
            };
        };
        responses: {
            /** @description Незавершённая сессия дейлика */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SessionSnapshot"];
                };
            };
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SessionSnapshot"];
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
    get_session_v1_sessions__session_id__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Идентификатор сессии */
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
                    "application/json": components["schemas"]["SessionSnapshot"];
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
    submit_guess_v1_sessions__session_id__guesses_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description Идентификатор сессии */
                session_id: string;
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
                    "application/json": components["schemas"]["MoveResult"];
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
    get_result_v1_sessions__session_id__result_get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Идентификатор сессии */
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
                    "application/json": components["schemas"]["SessionResult"];
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
    issue_clue_v1_sessions__session_id__rounds__index__clue_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Номер раунда с 0 */
                index: number;
                /** @description Идентификатор сессии */
                session_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ClueRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ClueOut"];
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
            /** @description Ресурс больше недоступен */
            410: {
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
    final_clue_v1_sessions__session_id__rounds__index__final_clue_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Номер раунда с 0 */
                index: number;
                /** @description Идентификатор сессии */
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
                    "application/json": components["schemas"]["ClueOut"];
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
            /** @description Ресурс больше недоступен */
            410: {
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
    give_up_v1_sessions__session_id__rounds__index__give_up_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description Номер раунда с 0 */
                index: number;
                /** @description Идентификатор сессии */
                session_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GiveUpRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["MoveResult"];
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
    skip_level_v1_sessions__session_id__skips_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description Идентификатор сессии */
                session_id: string;
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
                    "application/json": components["schemas"]["MoveResult"];
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
}
