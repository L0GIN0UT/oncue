/* eslint-disable */
// Сгенерировано `pnpm gen:api` из contracts/openapi/collections.json. Не править руками.

export interface paths {
    "/admin/v1/authors/{user_id}/restriction": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Ограничить автора
         * @description Не может создавать, править и публиковать сборники (опубликованные остаются).
         */
        post: operations["restrict_author_admin_v1_authors__user_id__restriction_post"];
        /**
         * Снять ограничение автора
         * @description Идемпотентно: без ограничения — тот же ответ `restricted: false`.
         */
        delete: operations["unrestrict_author_admin_v1_authors__user_id__restriction_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/collections": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Сборники (включая черновики и скрытые)
         * @description Поиск по названию и автору (нику); удалённые не показываются.
         */
        get: operations["admin_list_admin_v1_collections_get"];
        put?: never;
        /**
         * Создать кураторский сборник
         * @description Публикуется сразу (`collections.CollectionPublished`, `kind = curated`). Ошибки — как у публикации UGC: 422 `collections.publish_invalid` (`details.errors`).
         */
        post: operations["admin_create_admin_v1_collections_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/collections/{collection_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Сборник с составом
         * @description Опубликованный состав; у ни разу не опубликованного — состав черновика.
         */
        get: operations["admin_get_admin_v1_collections__collection_id__get"];
        /**
         * Новая версия кураторского сборника
         * @description 409 `collections.version_conflict` — `base_version` устарела (`details.current_version`); 409 `collections.not_curated` — UGC правит только автор; 422 `collections.publish_invalid`.
         */
        put: operations["admin_update_admin_v1_collections__collection_id__put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/collections/{collection_id}/featured": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Отметка «рекомендуемое»
         * @description Кураторские и UGC (D14); только опубликованные (409 `collections.not_published`). Без `rank` — в конце блока (1000). Событий нет; каталог `sort=featured`.
         */
        put: operations["admin_featured_admin_v1_collections__collection_id__featured_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/collections/{collection_id}/hide": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Скрыть сборник
         * @description Пропадает из каталога, старт сессии в game — 404; автор видит плашку. Событие `collections.CollectionUpdated` (`status: hidden`); повтор — без события. 409 `collections.not_published` — сборник ещё не публиковался.
         */
        post: operations["admin_hide_admin_v1_collections__collection_id__hide_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/collections/{collection_id}/unhide": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Показать скрытый сборник
         * @description Событие `collections.CollectionUpdated` (`status: published`).
         */
        post: operations["admin_unhide_admin_v1_collections__collection_id__unhide_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/collections": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Каталог сборников
         * @description Только опубликованные публичные (не скрытые) сборники. Поиск — по названию, описанию и словам тегов, не по единицам (D6). Сортировки: `popular` (лайки и завершения за окно), `new`, `likes`, `difficulty` (сложные раньше, без сложности — в конце), `featured` (только «рекомендуемые» по порядку). Без `q` — `Cache-Control: public, max-age=30`. 429 `collections.rate_limited` — лимит поиска с `q`.
         */
        get: operations["catalog_v1_collections_get"];
        put?: never;
        /**
         * Создать сборник (черновик)
         * @description Ошибки: 409 `collections.drafts_limit` (`details.max`), 422 `collections.title_invalid` (`details.reason` — код `ugc.*`), 403 `collections.author_restricted`.
         */
        post: operations["create_collection_v1_collections_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/collections/{collection_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Сборник
         * @description Публичный и unlisted (по ссылке). `items` — только автору, админу и игроку со статусом «пройден» текущей версии (D6), иначе `items: null`, `items_visible: false`; свои эмодзи единиц (`custom`) — только автору и админу. Скрытый — только автору и админу (`status: hidden`), остальным 404; гостю и анониму `me: null`.
         */
        get: operations["get_collection_v1_collections__collection_id__get"];
        put?: never;
        post?: never;
        /**
         * Удалить сборник
         * @description Повтор — 204. Опубликованный — событие `collections.CollectionDeleted`.
         */
        delete: operations["delete_collection_v1_collections__collection_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/collections/{collection_id}/draft": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Черновик сборника
         * @description Нет черновика — тело опубликованной версии и `revision = 0`.
         */
        get: operations["get_draft_v1_collections__collection_id__draft_get"];
        /**
         * Сохранить черновик
         * @description Оптимистичная блокировка (D2): `base_revision` ≠ текущей — 409 `collections.draft_conflict`, `details.current` — актуальный `DraftOut` (`revision`, `updated_at`, `client_label`, `body`). 409 `collections.hidden` — сборник скрыт модератором; 409 `collections.deleted` — удалён; 422 `collections.draft_invalid` (`details.errors` — `[{path, code}]`: `collections.items_too_many`, `collections.tags_too_many`).
         */
        put: operations["save_draft_v1_collections__collection_id__draft_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/collections/{collection_id}/favorite": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Добавить в избранное */
        put: operations["favorite_v1_collections__collection_id__favorite_put"];
        post?: never;
        /** Убрать из избранного */
        delete: operations["unfavorite_v1_collections__collection_id__favorite_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/collections/{collection_id}/like": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Лайкнуть сборник
         * @description Идемпотентно. Новый лайк — событие `collections.CollectionLiked`. 403 `collections.own_collection` — свой сборник; 429 `collections.rate_limited`.
         */
        put: operations["like_v1_collections__collection_id__like_put"];
        post?: never;
        /**
         * Снять лайк
         * @description Идемпотентно, событие не публикуется (D13).
         */
        delete: operations["unlike_v1_collections__collection_id__like_delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/collections/{collection_id}/publish": {
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
         * @description Новая `content_version` и событие `collections.CollectionPublished`. Ошибки: 409 `collections.draft_conflict` (ревизия устарела, `details.current`), 409 `collections.hidden`, 409 `collections.deleted`, 422 `collections.publish_invalid` (`details.errors` — `[{path, code, reason?}]`: `collections.items_too_few`, `collections.items_too_many`, `collections.item_duplicate`, `collections.item_domain_mismatch`, `collections.item_unavailable`, `collections.mode_invalid`, `collections.emoji_invalid`, `collections.emoji_too_short` (`min`), `collections.tag_unknown`, `collections.tags_too_many`, `collections.cover_unknown`, `collections.title_invalid`, `collections.description_invalid`; `reason` — код `ugc.*`), 429 `collections.publish_limit` (`details.retry_after`), 503 `collections.limiter_unavailable` (лимитер недоступен, fail-closed).
         */
        post: operations["publish_v1_collections__collection_id__publish_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/me/collections": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Мои сборники */
        get: operations["my_collections_v1_me_collections_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/me/favorites": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Избранные сборники
         * @description Опубликованные сборники по времени добавления (новые раньше).
         */
        get: operations["my_favorites_v1_me_favorites_get"];
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
        /**
         * Сведения о сервисе и словари конструктора
         * @description Лимиты (`GV_COLLECTIONS__LIMITS__*`), пресеты обложек, словарь тегов.
         */
        get: operations["meta_v1_meta_get"];
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
         * AdminCollectionDetail
         * @description Карточка в админке с составом и текстами.
         */
        AdminCollectionDetail: {
            author: components["schemas"]["AuthorOut"];
            /** Author Restricted */
            author_restricted: boolean;
            /**
             * Collection Id
             * Format: uuid
             */
            collection_id: string;
            cover?: components["schemas"]["CoverOut"] | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Default Mode Key */
            default_mode_key?: string | null;
            /** Description */
            description?: string | null;
            /** Difficulty */
            difficulty?: ("easy" | "medium" | "hard") | null;
            /**
             * Domain
             * @enum {string}
             */
            domain: "music" | "film";
            /** Draft Revision */
            draft_revision?: number | null;
            /** Featured */
            featured: boolean;
            /** Featured Rank */
            featured_rank?: number | null;
            hidden?: components["schemas"]["HiddenOut"] | null;
            /** Items */
            items: components["schemas"]["CollectionItemOut"][];
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
            /** Published At */
            published_at?: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "draft" | "published" | "hidden";
            /** Tags */
            tags: string[];
            /** Title */
            title: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Version */
            version?: number | null;
            /**
             * Visibility
             * @enum {string}
             */
            visibility: "public" | "unlisted";
        };
        /**
         * AdminCollectionOut
         * @description Сборник в админке (включая скрытые и черновики).
         */
        AdminCollectionOut: {
            author: components["schemas"]["AuthorOut"];
            /** Author Restricted */
            author_restricted: boolean;
            /**
             * Collection Id
             * Format: uuid
             */
            collection_id: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Difficulty */
            difficulty?: ("easy" | "medium" | "hard") | null;
            /**
             * Domain
             * @enum {string}
             */
            domain: "music" | "film";
            /** Featured */
            featured: boolean;
            /** Featured Rank */
            featured_rank?: number | null;
            hidden?: components["schemas"]["HiddenOut"] | null;
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
            /** Published At */
            published_at?: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "draft" | "published" | "hidden";
            /** Title */
            title: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Version */
            version?: number | null;
            /**
             * Visibility
             * @enum {string}
             */
            visibility: "public" | "unlisted";
        };
        /**
         * AdminCollectionPage
         * @description Список в админке.
         */
        AdminCollectionPage: {
            /** Items */
            items: components["schemas"]["AdminCollectionOut"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /**
         * AuthorOut
         * @description Автор: пользователь (ник из проекции identity), «Guessverse» или удалённый.
         */
        AuthorOut: {
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
         * CollectionCard
         * @description Карточка в каталоге и списках (без состава).
         */
        CollectionCard: {
            author: components["schemas"]["AuthorOut"];
            /**
             * Collection Id
             * Format: uuid
             */
            collection_id: string;
            cover?: components["schemas"]["CoverOut"] | null;
            /** Default Mode Key */
            default_mode_key: string;
            /**
             * Difficulty
             * @description null — завершений меньше min_plays (D9)
             */
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
            /**
             * Plays Count
             * @description Завершений пользователями (не автором)
             */
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
         * CollectionDetail
         * @description Страница сборника. ``items`` — ``null``, если состав текущему субъекту не открыт.
         */
        CollectionDetail: {
            author: components["schemas"]["AuthorOut"];
            /**
             * Collection Id
             * Format: uuid
             */
            collection_id: string;
            cover?: components["schemas"]["CoverOut"] | null;
            /** Default Mode Key */
            default_mode_key: string;
            /** Description */
            description?: string | null;
            /**
             * Difficulty
             * @description null — завершений меньше min_plays (D9)
             */
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
            items?: components["schemas"]["CollectionItemOut"][] | null;
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
            me?: components["schemas"]["MeOut"] | null;
            /**
             * Plays Count
             * @description Завершений пользователями (не автором)
             */
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
            /**
             * Version
             * @description content_version
             */
            version: number;
            /**
             * Visibility
             * @enum {string}
             */
            visibility: "public" | "unlisted";
        };
        /**
         * CollectionItemOut
         * @description Единица состава: только по D6 (автор, админ, прошедший игрок).
         */
        CollectionItemOut: {
            /**
             * Available
             * @description false — единица заблокирована или удалена в catalog
             */
            available: boolean;
            custom?: components["schemas"]["CustomClueOut"] | null;
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
         * CollectionPage
         * @description Страница каталога (keyset-курсор).
         */
        CollectionPage: {
            /** Items */
            items: components["schemas"]["CollectionCard"][];
            /** Next Cursor */
            next_cursor?: string | null;
        };
        /**
         * CollectionsMeta
         * @description Сведения о сервисе и словари конструктора (``limits``, ``cover_presets``, ``tags``).
         */
        CollectionsMeta: {
            /** Cover Presets */
            cover_presets?: components["schemas"]["CoverPresetOut"][] | null;
            limits?: components["schemas"]["MetaLimits"] | null;
            /** Service */
            service: string;
            /** Status */
            status: string;
            /** Tags */
            tags?: components["schemas"]["TagOut"][] | null;
        };
        /**
         * Cover
         * @description Обложка — пресет из ``GET /v1/meta`` (градиент и эмодзи из конфига), D6.
         */
        Cover: {
            /** Preset */
            preset: string;
        };
        /**
         * CoverOut
         * @description Обложка сборника.
         */
        CoverOut: {
            /** Preset */
            preset: string;
        };
        /**
         * CoverPresetOut
         * @description Пресет обложки.
         */
        CoverPresetOut: {
            /** Emoji */
            emoji: string;
            /**
             * Gradient
             * @description Цвета CSS (#rrggbb) по порядку
             */
            gradient: string[];
            /** Id */
            id: string;
        };
        /**
         * CreateCollectionRequest
         * @description Новый сборник: создаётся черновик ``revision = 1`` с этим названием.
         */
        CreateCollectionRequest: {
            /**
             * Domain
             * @enum {string}
             */
            domain: "music" | "film";
            /** Title */
            title: string;
        };
        /**
         * CuratedCollectionIn
         * @description Кураторский сборник из YAML-редактора админки (D14): публикуется сразу.
         *
         *     Автор отображается как «Guessverse» (``kind = curated``, ``author_id = null``);
         *     единицы — по ``media_item_id``, проверяются как при публикации UGC.
         */
        CuratedCollectionIn: {
            cover?: components["schemas"]["Cover"] | null;
            /** Default Mode Key */
            default_mode_key: string;
            /** Description */
            description?: string | null;
            /**
             * Domain
             * @enum {string}
             */
            domain: "music" | "film";
            /** Items */
            items: components["schemas"]["DraftItem"][];
            /** Reason */
            reason: string;
            /** Tags */
            tags?: string[];
            /** Title */
            title: string;
            /**
             * Visibility
             * @default public
             * @enum {string}
             */
            visibility: "public" | "unlisted";
        };
        /**
         * CuratedCollectionUpdate
         * @description Новая версия кураторского сборника (оптимистичная блокировка по ``base_version``).
         */
        CuratedCollectionUpdate: {
            /** Base Version */
            base_version: number;
            cover?: components["schemas"]["Cover"] | null;
            /** Default Mode Key */
            default_mode_key: string;
            /** Description */
            description?: string | null;
            /** Items */
            items: components["schemas"]["DraftItem"][];
            /** Reason */
            reason: string;
            /** Tags */
            tags?: string[];
            /** Title */
            title: string;
            /**
             * Visibility
             * @default public
             * @enum {string}
             */
            visibility: "public" | "unlisted";
        };
        /**
         * CustomClue
         * @description Свои загадки автора к единице (ADR 0029): в 3b только эмодзи.
         *
         *     Эмодзи — строго RGI (``gv_text.emoji.is_strict_emoji``), не больше
         *     ``limits.emoji_max_graphemes`` графем, без текста и пробелов; иначе при публикации —
         *     ``collections.emoji_invalid``. Меньше ``limits.emoji_min_graphemes`` графем —
         *     ``collections.emoji_too_short``: профиль ``emoji`` media-pipeline открывает до 6 эмодзи
         *     и из меньшего числа набор не строит.
         */
        CustomClue: {
            /** Emoji */
            emoji?: string | null;
        };
        /**
         * CustomClueOut
         * @description Свои загадки единицы (видит только автор и админ).
         */
        CustomClueOut: {
            /** Emoji */
            emoji?: string | null;
        };
        /**
         * DraftBody
         * @description Тело черновика: всё, что автор правит в конструкторе (домен фиксирован при создании).
         *
         *     Черновик сохраняется и неполным (меньше ``items_min`` единиц, без режима); полная
         *     проверка — при публикации.
         */
        DraftBody: {
            cover?: components["schemas"]["Cover"] | null;
            /** Default Mode Key */
            default_mode_key?: string | null;
            /** Description */
            description?: string | null;
            /** Items */
            items?: components["schemas"]["DraftItem"][];
            /** Tags */
            tags?: string[];
            /** Title */
            title: string;
            /**
             * Visibility
             * @default public
             * @enum {string}
             */
            visibility: "public" | "unlisted";
        };
        /**
         * DraftItem
         * @description Единица черновика в порядке автора.
         */
        DraftItem: {
            custom?: components["schemas"]["CustomClue"] | null;
            /**
             * Media Item Id
             * Format: uuid
             */
            media_item_id: string;
        };
        /**
         * DraftOut
         * @description Черновик сборника (виден только автору; чужой — 404 ``collections.not_found``).
         */
        DraftOut: {
            /**
             * Base Version
             * @description content_version, поверх которой начат черновик
             */
            base_version?: number | null;
            body: components["schemas"]["DraftBody"];
            /**
             * Client Label
             * @description Подпись устройства последнего сохранения (экран конфликта)
             */
            client_label?: string | null;
            /**
             * Collection Id
             * Format: uuid
             */
            collection_id: string;
            /**
             * Domain
             * @enum {string}
             */
            domain: "music" | "film";
            /**
             * Revision
             * @description 0 — черновика нет, тело взято из опубликованной версии
             */
            revision: number;
            /**
             * Status
             * @enum {string}
             */
            status: "draft" | "published" | "hidden";
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /**
         * FavoriteOut
         * @description Состояние избранного после запроса.
         */
        FavoriteOut: {
            /** Favorite */
            favorite: boolean;
        };
        /**
         * FeaturedRequest
         * @description Отметка «рекомендуемое» (кураторские и UGC, D14).
         */
        FeaturedRequest: {
            /** Featured */
            featured: boolean;
            /**
             * Rank
             * @description Порядок в блоке, меньше — выше
             */
            rank?: number | null;
            /** Reason */
            reason: string;
        };
        /**
         * HiddenOut
         * @description Кто и почему скрыл сборник (аудит; автору показывается только факт скрытия).
         */
        HiddenOut: {
            /** Actor Id */
            actor_id?: string | null;
            /**
             * At
             * Format: date-time
             */
            at: string;
            /** Reason */
            reason: string;
        };
        /**
         * LikeOut
         * @description Состояние лайка после запроса (повтор — тот же ответ, D13).
         */
        LikeOut: {
            /** Liked */
            liked: boolean;
            /** Likes Count */
            likes_count: number;
        };
        /**
         * MeOut
         * @description Отношение текущего пользователя к сборнику (гостю и анониму — ``me: null``).
         */
        MeOut: {
            /**
             * Completed
             * @description Есть завершение текущей версии (открывает состав)
             */
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
         * MetaLimits
         * @description Лимиты конструктора (``GV_COLLECTIONS__LIMITS__*``).
         */
        MetaLimits: {
            /** Description Max */
            description_max: number;
            /** Drafts Max */
            drafts_max: number;
            /** Emoji Max Graphemes */
            emoji_max_graphemes: number;
            /**
             * Emoji Min Graphemes
             * @description Минимум графем своих эмодзи (иначе `collections.emoji_too_short`)
             */
            emoji_min_graphemes: number;
            /** Items Max */
            items_max: number;
            /** Items Min */
            items_min: number;
            /** Publish Per Day */
            publish_per_day: number;
            /** Tags Max */
            tags_max: number;
            /** Title Max */
            title_max: number;
        };
        /**
         * MyCollectionOut
         * @description Сборник в «моих сборниках».
         */
        MyCollectionOut: {
            /**
             * Collection Id
             * Format: uuid
             */
            collection_id: string;
            /**
             * Domain
             * @enum {string}
             */
            domain: "music" | "film";
            /** Draft Revision */
            draft_revision?: number | null;
            /** Has Draft */
            has_draft: boolean;
            /**
             * Hidden By Moderator
             * @description Плашка «скрыт модератором»
             */
            hidden_by_moderator: boolean;
            /** Items Count */
            items_count: number;
            /**
             * Kind
             * @enum {string}
             */
            kind: "ugc" | "curated";
            /** Likes Count */
            likes_count: number;
            /** Published At */
            published_at?: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "draft" | "published" | "hidden";
            /** Title */
            title: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /**
             * Version
             * @description Опубликованная content_version
             */
            version?: number | null;
            /**
             * Visibility
             * @enum {string}
             */
            visibility: "public" | "unlisted";
        };
        /**
         * MyCollectionsPage
         * @description Мои сборники (keyset-курсор по ``updated_at``).
         */
        MyCollectionsPage: {
            /** Items */
            items: components["schemas"]["MyCollectionOut"][];
            /** Next Cursor */
            next_cursor?: string | null;
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
         * @description Опубликовать ревизию черновика (новая ``content_version``).
         */
        PublishRequest: {
            /** Draft Revision */
            draft_revision: number;
        };
        /**
         * ReasonRequest
         * @description Действие модератора с причиной (аудит).
         */
        ReasonRequest: {
            /** Reason */
            reason: string;
        };
        /**
         * RestrictionOut
         * @description Ограничение автора: не может создавать, править и публиковать сборники.
         */
        RestrictionOut: {
            /** Actor Id */
            actor_id?: string | null;
            /** At */
            at?: string | null;
            /** Reason */
            reason?: string | null;
            /** Restricted */
            restricted: boolean;
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
        };
        /**
         * SaveDraftRequest
         * @description Сохранить черновик с оптимистичной блокировкой (D2).
         *
         *     ``base_revision`` — ревизия, которую клиент правил; не совпала с текущей — 409
         *     ``collections.draft_conflict``, ``details.current`` — актуальный ``DraftOut``.
         */
        SaveDraftRequest: {
            /** Base Revision */
            base_revision: number;
            body: components["schemas"]["DraftBody"];
            /** Client Label */
            client_label?: string | null;
        };
        /**
         * TagOut
         * @description Тег из словаря (свободных тегов нет).
         */
        TagOut: {
            /** Domains */
            domains: ("music" | "film")[];
            /** Key */
            key: string;
            /** Title Key */
            title_key: string;
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
    restrict_author_admin_v1_authors__user_id__restriction_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description Пользователь-автор */
                user_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ReasonRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RestrictionOut"];
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
    unrestrict_author_admin_v1_authors__user_id__restriction_delete: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description Пользователь-автор */
                user_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ReasonRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RestrictionOut"];
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
    admin_list_admin_v1_collections_get: {
        parameters: {
            query?: {
                cursor?: string | null;
                kind?: ("ugc" | "curated") | null;
                limit?: number;
                q?: string | null;
                status?: ("draft" | "published" | "hidden") | null;
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
                    "application/json": components["schemas"]["AdminCollectionPage"];
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
    admin_create_admin_v1_collections_post: {
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
                "application/json": components["schemas"]["CuratedCollectionIn"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AdminCollectionDetail"];
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
    admin_get_admin_v1_collections__collection_id__get: {
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
                    "application/json": components["schemas"]["AdminCollectionDetail"];
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
    admin_update_admin_v1_collections__collection_id__put: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description Идентификатор сборника */
                collection_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CuratedCollectionUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AdminCollectionDetail"];
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
    admin_featured_admin_v1_collections__collection_id__featured_put: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description Идентификатор сборника */
                collection_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["FeaturedRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AdminCollectionOut"];
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
    admin_hide_admin_v1_collections__collection_id__hide_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description Идентификатор сборника */
                collection_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ReasonRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AdminCollectionOut"];
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
    admin_unhide_admin_v1_collections__collection_id__unhide_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description Идентификатор сборника */
                collection_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ReasonRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AdminCollectionOut"];
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
    catalog_v1_collections_get: {
        parameters: {
            query?: {
                curated?: boolean | null;
                cursor?: string | null;
                difficulty?: ("easy" | "medium" | "hard") | null;
                domain?: ("music" | "film") | null;
                limit?: number;
                mode?: string | null;
                q?: string | null;
                sort?: "popular" | "new" | "likes" | "difficulty" | "featured";
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
                    "application/json": components["schemas"]["CollectionPage"];
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
    create_collection_v1_collections_post: {
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
                "application/json": components["schemas"]["CreateCollectionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
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
    get_collection_v1_collections__collection_id__get: {
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
                    "application/json": components["schemas"]["CollectionDetail"];
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
    delete_collection_v1_collections__collection_id__delete: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description Идентификатор сборника */
                collection_id: string;
            };
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
    get_draft_v1_collections__collection_id__draft_get: {
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
    save_draft_v1_collections__collection_id__draft_put: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description Идентификатор сборника */
                collection_id: string;
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
    favorite_v1_collections__collection_id__favorite_put: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
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
                    "application/json": components["schemas"]["FavoriteOut"];
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
    unfavorite_v1_collections__collection_id__favorite_delete: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
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
                    "application/json": components["schemas"]["FavoriteOut"];
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
    like_v1_collections__collection_id__like_put: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
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
                    "application/json": components["schemas"]["LikeOut"];
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
    unlike_v1_collections__collection_id__like_delete: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
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
                    "application/json": components["schemas"]["LikeOut"];
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
    publish_v1_collections__collection_id__publish_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description Идентификатор сборника */
                collection_id: string;
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
                    "application/json": components["schemas"]["CollectionDetail"];
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
    my_collections_v1_me_collections_get: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
                status?: ("draft" | "published" | "hidden") | null;
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
                    "application/json": components["schemas"]["MyCollectionsPage"];
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
    my_favorites_v1_me_favorites_get: {
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
                    "application/json": components["schemas"]["CollectionsMeta"];
                };
            };
        };
    };
}
