/* eslint-disable */
// Сгенерировано `pnpm gen:api` из contracts/openapi/progression.json. Не править руками.

export interface paths {
    "/admin/v1/badges": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Бейджи
         * @description Бейджи действующего конфига с числом выданных. Правка - через черновик.
         */
        get: operations["badges_admin_v1_badges_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/configs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Версии конфига
         * @description Черновик, действующая и архивные версии, новые первыми.
         */
        get: operations["versions_admin_v1_configs_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/configs/{version}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Версия конфига
         * @description Тело версии. Ошибки: 404 `progression.config_version_not_found`.
         */
        get: operations["version_admin_v1_configs__version__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/configs/{version}/rollback": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Откатить к версии
         * @description Копия старой версии становится черновиком (заменяет текущий). Ошибки: 404 `progression.config_version_not_found`, 409 `progression.config_never_published`.
         */
        post: operations["rollback_admin_v1_configs__version__rollback_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/configs/draft": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Сохранить черновик
         * @description Заменяет черновик (он один); конфиг проверяется целиком. Ошибки: 422 `progression.config_invalid` (`details.errors` - пути и причины).
         */
        put: operations["save_draft_admin_v1_configs_draft_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/configs/draft/publish": {
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
         * @description Новая версия действует для новых проводок, прошлое не пересчитывается; прежняя действующая уходит в архив. Ошибки: 404 `progression.config_version_not_found`, 409 `progression.config_not_draft`, 409 `progression.draft_changed` (тело черновика изменилось).
         */
        post: operations["publish_admin_v1_configs_draft_publish_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/items": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Предметы */
        get: operations["items_admin_v1_items_get"];
        put?: never;
        /**
         * Создать предмет
         * @description Без картинки предмет неактивен. Слот и редкость - из секции `shop` действующего конфига (иначе 422 `progression.item_slot_unknown` или `progression.item_rarity_unknown`); нужно `level_required` или `price` (422 `progression.item_unlock_required`); повтор `key` - 409 `progression.item_key_taken`.
         */
        post: operations["create_item_admin_v1_items_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/items/{item_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Изменить предмет
         * @description Цена и условия действуют для новых покупок; владельцы не затрагиваются. Активировать без картинки нельзя - 422 `progression.item_media_missing`.
         */
        put: operations["update_item_admin_v1_items__item_id__put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/items/{item_id}/media": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Загрузить картинку или анимацию предмета
         * @description multipart: `file`, `reason`. Статика - WebP/PNG, анимация - animated WebP (ADR 0046); проверка сигнатуры, декодирование, лимиты размера, кадров, длительности и стороны из секции `shop.media` конфига, постер - первый кадр. Ошибки: 422 `progression.item_media_invalid`, 413 `progression.item_media_too_large`.
         */
        put: operations["upload_item_media_admin_v1_items__item_id__media_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/ledger/{op_id}/reverse": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Сторнировать проводку
         * @description Встречная проводка со ссылкой `reversal_of`; журнал не правится; только журнал Версов, только кредиты. Ошибки: 404 `progression.ledger_entry_not_found`, 409 `progression.already_reversed`, 409 `progression.not_reversible` (дебет, сторно, проводка опыта), 409 `progression.insufficient_balance` (баланс ушёл бы ниже нуля).
         */
        post: operations["reverse_admin_v1_ledger__op_id__reverse_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/packs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Паки */
        get: operations["packs_admin_v1_packs_get"];
        put?: never;
        /**
         * Создать пак
         * @description Только модель: витрина и покупка скрыты, пока `billing.enabled=false`.
         */
        post: operations["create_pack_admin_v1_packs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/packs/{pack_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Заменить пак */
        put: operations["update_pack_admin_v1_packs__pack_id__put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/promo-codes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Партии промокодов
         * @description Без самих кодов: в БД только хеш.
         */
        get: operations["promo_batches_admin_v1_promo_codes_get"];
        put?: never;
        /**
         * Сгенерировать партию промокодов
         * @description Коды генерируются сервером (длина - `promo.code_length` конфига) и возвращаются ОДИН раз. Награда - предмет или Версы; для предмета он должен быть активен (422 `progression.item_not_found`). Повтор с тем же `Idempotency-Key` не создаёт вторую партию и не раскрывает коды повторно (409 `progression.codes_already_shown`).
         */
        post: operations["create_promo_admin_v1_promo_codes_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/promo-codes/{promo_id}/revoke": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Отозвать партию
         * @description Новые активации невозможны; выданные награды не отзываются.
         */
        post: operations["revoke_promo_admin_v1_promo_codes__promo_id__revoke_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/quests": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Задания
         * @description Задания действующего конфига с числом выполнений. Правка - через черновик.
         */
        get: operations["quests_admin_v1_quests_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/users/{user_id}/ledger": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Журнал игрока
         * @description Проводки Версов, новые первыми, курсорная пагинация. Ошибки: 404 `progression.user_not_found`.
         */
        get: operations["ledger_admin_v1_users__user_id__ledger_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/admin/v1/users/{user_id}/ledger/grant": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Выдать Версы вручную
         * @description Проводка `admin:{Idempotency-Key}`; предел - `admin.grant_max_amount` и `admin.grant_max_per_user_day` конфига. Ошибки: 404 `progression.user_not_found`, 422 `progression.grant_limit_exceeded`, 422 `progression.reason_too_short`.
         */
        post: operations["grant_admin_v1_users__user_id__ledger_grant_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/appearance": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Оформление игроков
         * @description Публичное оформление (рамка, титул, фон, аватар) до 12 игроков за вызов: ник и аватар в матче и в профиле. Только пользователь с аккаунтом; неизвестные id пропускаются, дубли схлопываются; больше 12 - 422.
         */
        get: operations["players_appearance_v1_appearance_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/me/appearance": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Моё оформление
         * @description По одному предмету на слот; пустой слот - `item = null`.
         */
        get: operations["my_appearance_v1_me_appearance_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/me/appearance/{slot}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Надеть или снять предмет
         * @description `item_id = null` снимает. Предмет должен быть в инвентаре и подходить слоту. Идемпотентно по природе операции. Ошибки: 404 `progression.slot_not_found`, 404 `progression.item_not_owned`, 422 `progression.item_slot_mismatch`.
         */
        put: operations["equip_v1_me_appearance__slot__put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/me/avatar": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Моя аватарка
         * @description Куплено ли право, действующий аватар, последняя заявка и лимиты загрузки.
         */
        get: operations["my_avatar_v1_me_avatar_get"];
        put?: never;
        /**
         * Загрузить свою аватарку
         * @description multipart, поле `file`. Нужен купленный товар (403 `progression.avatar_not_entitled`). Файл уходит в media-pipeline (проверка, пересжатие, UGC-ассет `pending`); до решения модератора виден прежний аватар. Новая заявка отменяет предыдущую `pending`. Ошибки файла: 422 `ugc.invalid_image`, 413 `ugc.too_large`.
         */
        post: operations["submit_avatar_v1_me_avatar_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/me/badges": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Мои бейджи
         * @description Все бейджи конфига: полученные и с прогрессом.
         */
        get: operations["my_badges_v1_me_badges_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/me/inventory": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Мой инвентарь
         * @description Предметы игрока; просроченные временные предметы скрыты.
         */
        get: operations["my_inventory_v1_me_inventory_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/me/progress": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Мой прогресс
         * @description Уровень, опыт, потолок соло, серии дейлика по доменам. Ошибки: 503 `progression.config_unavailable` — нет опубликованного конфига.
         */
        get: operations["my_progress_v1_me_progress_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/me/quests": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Мои задания
         * @description Дневные, недельные и разовые задания с прогрессом; назначение — снимком на период (смена конфига действует со следующего периода). Ошибки: 503 `progression.config_unavailable`.
         */
        get: operations["my_quests_v1_me_quests_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/me/wallet": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Мой кошелёк
         * @description Версы по бакетам `earned` и `paid` и последние проводки.
         */
        get: operations["my_wallet_v1_me_wallet_get"];
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
    "/v1/promo/redeem": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Погасить промокод
         * @description Награда - предмет или Версы. Повтор того же кода тем же игроком - та же награда (`repeated = true`). Неверный, просроченный и исчерпанный код дают одинаковый 404 `progression.promo_invalid`; лимит попыток по игроку и IP - 429 `progression.promo_rate_limited` с `Retry-After`. Гостю - 403.
         */
        post: operations["redeem_v1_promo_redeem_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/shop/items": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Витрина
         * @description Активные предметы с состоянием для игрока (`locked | available | owned`); фильтр по слоту. Условия открытия - по уровню и/или за Версы.
         */
        get: operations["shop_items_v1_shop_items_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/shop/items/{item_id}/purchase": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Купить предмет
         * @description Одна транзакция: проводка `CurrencyDebited` (`op_id = shop:{user}:{item}`), строка инвентаря, outbox `ItemPurchased`. Пустое тело; цена и условия - только на сервере. Повтор - та же выдача (`repeated = true`). Ошибки: 409 `progression.insufficient_funds`, 409 `progression.already_owned`, 403 `progression.item_not_available`, 404 `progression.item_not_found`.
         */
        post: operations["purchase_v1_shop_items__item_id__purchase_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/shop/packs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Паки косметики
         * @description Скрыты, пока `billing.enabled=false`: 404 `progression.billing_disabled`.
         */
        get: operations["shop_packs_v1_shop_packs_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/users/{user_id}/progress": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Прогресс игрока
         * @description Публичный профиль: уровень, опыт, бейджи. Ошибки: 404 `progression.user_not_found`.
         */
        get: operations["user_progress_v1_users__user_id__progress_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/wheel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Колесо
         * @description Секторы с весами и шансами, спин за сегодня и время обновления.
         */
        get: operations["wheel_v1_wheel_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/wheel/spin": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Крутить колесо
         * @description Тело пустое (любое тело — 422). Один спин в сутки (`progression.timezone`): сектор выбирает сервер по весам, приз выдаётся в той же транзакции. Повтор за те же сутки возвращает тот же результат (`repeated = true`). Ошибки: 409 `progression.wheel_disabled`.
         */
        post: operations["spin_v1_wheel_spin_post"];
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
         * AdminBadgeCard
         * @description Бейдж действующего конфига со статистикой.
         */
        AdminBadgeCard: {
            /** Enabled */
            enabled: boolean;
            /** Key */
            key: string;
            /** Title */
            title: string;
            /**
             * Unlocked Total
             * @description null - счётчиков ещё нет (данные появятся с бейджами)
             */
            unlocked_total?: number | null;
        };
        /**
         * AdminBadgeList
         * @description Бейджи действующего конфига.
         */
        AdminBadgeList: {
            /** Config Version */
            config_version: number;
            /** Items */
            items: components["schemas"]["AdminBadgeCard"][];
        };
        /**
         * AdminItem
         * @description Предмет в админке.
         */
        AdminItem: {
            /** Active */
            active: boolean;
            /**
             * Asset Url
             * @description null - картинка не загружена
             */
            asset_url: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Key */
            key: string;
            /** Level Required */
            level_required: number | null;
            /**
             * Media Kind
             * @enum {string}
             */
            media_kind: "static" | "animated";
            /**
             * Owners
             * @description Сколько игроков владеют предметом
             */
            owners: number;
            /** Poster Url */
            poster_url: string | null;
            /** Price */
            price: number | null;
            /** Rarity */
            rarity: string;
            /** Slot */
            slot: string;
            /** Title */
            title: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /**
         * AdminItemPage
         * @description Страница предметов (курсор).
         */
        AdminItemPage: {
            /** Items */
            items: components["schemas"]["AdminItem"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /**
         * AdminLedgerEntry
         * @description Проводка в админке.
         */
        AdminLedgerEntry: {
            /**
             * Actor Id
             * @description Админ; для автоматических — null
             */
            actor_id: string | null;
            /** Amount */
            amount: number;
            /**
             * At
             * Format: date-time
             */
            at: string;
            /**
             * Bucket
             * @enum {string}
             */
            bucket: "earned" | "paid";
            /** Config Version */
            config_version: number;
            /**
             * Direction
             * @enum {string}
             */
            direction: "credit" | "debit";
            /** Op Id */
            op_id: string;
            /** Reason */
            reason: string;
            /** Reversal Of */
            reversal_of?: string | null;
            /** Source Ref */
            source_ref: string;
        };
        /**
         * AdminLedgerPage
         * @description Страница журнала игрока, новые первыми.
         */
        AdminLedgerPage: {
            /** Earned */
            earned: number;
            /** Items */
            items: components["schemas"]["AdminLedgerEntry"][];
            /** Next Cursor */
            next_cursor?: string | null;
            /** Paid */
            paid: number;
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
        };
        /**
         * AdminPack
         * @description Пак в админке.
         */
        AdminPack: {
            /** Active */
            active: boolean;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Item Ids */
            item_ids: string[];
            /** Key */
            key: string;
            /** Price Kopecks */
            price_kopecks: number;
            /** Style */
            style: string;
            /** Title */
            title: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Verses Amount */
            verses_amount: number;
        };
        /**
         * AdminPackList
         * @description Паки.
         */
        AdminPackList: {
            /** Items */
            items: components["schemas"]["AdminPack"][];
        };
        /**
         * AdminQuestCard
         * @description Задание действующего конфига со статистикой.
         */
        AdminQuestCard: {
            /**
             * Completed Total
             * @description null - счётчиков ещё нет (данные появятся с заданиями)
             */
            completed_total?: number | null;
            /** Enabled */
            enabled: boolean;
            /** Key */
            key: string;
            /**
             * Period
             * @enum {string}
             */
            period: "daily" | "weekly" | "one_time";
            reward: components["schemas"]["Reward"];
            /** Title */
            title: string;
        };
        /**
         * AdminQuestList
         * @description Задания действующего конфига.
         */
        AdminQuestList: {
            /** Config Version */
            config_version: number;
            /** Items */
            items: components["schemas"]["AdminQuestCard"][];
        };
        /**
         * Appearance
         * @description Оформление профиля: по одному предмету на слот.
         */
        Appearance: {
            /** Slots */
            slots: components["schemas"]["AppearanceSlot"][];
        };
        /**
         * AppearanceSlot
         * @description Слот оформления.
         */
        AppearanceSlot: {
            /** @description null - слот пуст */
            item: components["schemas"]["ItemView"] | null;
            /** Slot */
            slot: string;
        };
        /**
         * AvatarLimits
         * @description Лимиты загрузки (из конфига media-pipeline).
         */
        AvatarLimits: {
            /** Formats */
            formats: string[];
            /** Max Bytes */
            max_bytes: number;
            /** Max Side Px */
            max_side_px: number;
            /** Min Side Px */
            min_side_px: number;
        };
        /**
         * AvatarRequestView
         * @description Заявка на свою аватарку.
         */
        AvatarRequestView: {
            /**
             * Asset Id
             * Format: uuid
             */
            asset_id: string;
            /**
             * Reason Code
             * @description Код причины отклонения; иначе null
             */
            reason_code: string | null;
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
         * BadgeRef
         * @description Бейдж в публичном профиле.
         */
        BadgeRef: {
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
         * BadgeView
         * @description Бейдж со своим прогрессом.
         */
        BadgeView: {
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
            /**
             * Unlocked At
             * @description null — не получен
             */
            unlocked_at: string | null;
        };
        /** Body_submit_avatar_v1_me_avatar_post */
        Body_submit_avatar_v1_me_avatar_post: {
            /**
             * File
             * @description Изображение
             */
            file: string;
        };
        /** Body_upload_item_media_admin_v1_items__item_id__media_put */
        Body_upload_item_media_admin_v1_items__item_id__media_put: {
            /**
             * File
             * @description Картинка или анимация
             */
            file: string;
            /** Reason */
            reason: string;
        };
        /**
         * BoostView
         * @description Буст опыта (с колеса): множитель на N онлайн-матчей.
         */
        BoostView: {
            /** Matches Left */
            matches_left: number;
            /** Multiplier */
            multiplier: number;
        };
        /**
         * ConfigVersionBody
         * @description Версия конфига с телом.
         */
        ConfigVersionBody: {
            card: components["schemas"]["ConfigVersionCard"];
            /**
             * Config
             * @description По `contracts/progression/progression_config.v1.json`
             */
            config: {
                [key: string]: components["schemas"]["JsonValue"];
            };
        };
        /**
         * ConfigVersionCard
         * @description Версия конфига progression.
         */
        ConfigVersionCard: {
            /**
             * Body Sha256
             * @description sha256 канонического JSON тела; публикация привязана к нему
             */
            body_sha256: string;
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
         * ConfigVersionList
         * @description Версии конфига, новые первыми.
         */
        ConfigVersionList: {
            /** Items */
            items: components["schemas"]["ConfigVersionCard"][];
        };
        /**
         * CreatedPromo
         * @description Партия кодов. `codes` показывается ОДИН раз - потом в БД только хеш.
         */
        CreatedPromo: {
            /** Codes */
            codes: string[];
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Label */
            label: string;
        };
        /**
         * CreateItemRequest
         * @description Создать предмет (без картинки - неактивным).
         */
        CreateItemRequest: {
            /**
             * Active
             * @description Активный предмет виден на витрине; нужна загруженная картинка
             * @default false
             */
            active: boolean;
            /** Key */
            key: string;
            /** Level Required */
            level_required?: number | null;
            /**
             * Media Kind
             * @enum {string}
             */
            media_kind: "static" | "animated";
            /** Price */
            price?: number | null;
            /** Rarity */
            rarity: string;
            /** Reason */
            reason: string;
            /** Slot */
            slot: string;
            /** Title */
            title: string;
        };
        /**
         * CreatePromoRequest
         * @description Сгенерировать партию кодов с общими условиями.
         */
        CreatePromoRequest: {
            /**
             * Count
             * @description Сколько кодов сгенерировать
             */
            count: number;
            /** Expires At */
            expires_at?: string | null;
            /**
             * Label
             * @description Название партии (для админки; в уведомления не попадает)
             */
            label: string;
            /**
             * Max Uses
             * @description Лимит активаций на каждый код
             */
            max_uses: number;
            /**
             * Per User Once
             * @description Один код - один раз на игрока
             * @default true
             */
            per_user_once: boolean;
            /** Reason */
            reason: string;
            reward: components["schemas"]["PromoReward"];
        };
        /**
         * DailyStreakView
         * @description Серия дейлика по домену.
         */
        DailyStreakView: {
            /** Best */
            best: number;
            /**
             * Current
             * @description Подряд пройденных дней; обрывается пропуском дня
             */
            current: number;
            /**
             * Domain
             * @enum {string}
             */
            domain: "music" | "film";
            /**
             * Last Day
             * @description Последний пройденный день; null — не проходил
             */
            last_day: string | null;
        };
        /**
         * EquipRequest
         * @description Надеть предмет в слот или снять (`item_id = null`).
         */
        EquipRequest: {
            /**
             * Item Id
             * @description null - снять; предмет должен быть в инвентаре и подходить слоту
             */
            item_id: string | null;
        };
        /**
         * GrantRequest
         * @description Ручная выдача Версов.
         */
        GrantRequest: {
            /**
             * Amount
             * @description Версы; верхний предел — `admin.grant_max_amount`
             */
            amount: number;
            /**
             * Bucket
             * @description В 5a ручная выдача только в `earned` (`paid` - с 5b)
             * @default earned
             * @constant
             */
            bucket: "earned";
            /** Reason */
            reason: string;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /**
         * InventoryEntry
         * @description Предмет в инвентаре.
         */
        InventoryEntry: {
            /**
             * Acquired At
             * Format: date-time
             */
            acquired_at: string;
            /** Equipped */
            equipped: boolean;
            /**
             * Expires At
             * @description Временный предмет (5d); в 5b всегда null
             */
            expires_at: string | null;
            item: components["schemas"]["ItemView"];
            /**
             * Source
             * @enum {string}
             */
            source: "purchase" | "level" | "wheel" | "promo" | "pack" | "admin" | "event";
        };
        /**
         * ItemView
         * @description Предмет косметики.
         */
        ItemView: {
            /**
             * Asset Url
             * @description Публичный адрес картинки или анимации (animated WebP)
             */
            asset_url: string;
            /**
             * Event Key
             * @description Сезонное событие (5d); в 5b всегда null
             */
            event_key: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Key */
            key: string;
            /**
             * Level Required
             * @description Открывается с уровня; null - по уровню нет
             */
            level_required: number | null;
            /**
             * Media Kind
             * @enum {string}
             */
            media_kind: "static" | "animated";
            /**
             * Poster Url
             * @description Статичный кадр анимации (для prefers-reduced-motion); у static - null
             */
            poster_url: string | null;
            /**
             * Price
             * @description Цена в Версах; null - не продаётся
             */
            price: number | null;
            /** Rarity */
            rarity: string;
            /** Slot */
            slot: string;
            /** Title */
            title: string;
        };
        JsonValue: unknown;
        /**
         * LedgerEntryView
         * @description Проводка кошелька.
         */
        LedgerEntryView: {
            /** Amount */
            amount: number;
            /**
             * At
             * Format: date-time
             */
            at: string;
            /**
             * Bucket
             * @enum {string}
             */
            bucket: "earned" | "paid";
            /**
             * Direction
             * @enum {string}
             */
            direction: "credit" | "debit";
            /** Op Id */
            op_id: string;
            /** Reason */
            reason: string;
        };
        /**
         * LedgerOperation
         * @description Итог ручной операции: проводка и баланс после неё.
         */
        LedgerOperation: {
            /** Balance After */
            balance_after: number;
            entry: components["schemas"]["AdminLedgerEntry"];
            /**
             * User Id
             * Format: uuid
             * @description Владелец проводки
             */
            user_id: string;
        };
        /**
         * MyAvatar
         * @description Своя аватарка: право, действующий аватар, последняя заявка.
         */
        MyAvatar: {
            /**
             * Current Url
             * @description Одобренная аватарка; null - используется предмет слота
             */
            current_url: string | null;
            /**
             * Entitled
             * @description Куплен товар «своя аватарка»
             */
            entitled: boolean;
            limits: components["schemas"]["AvatarLimits"];
            /** @description Последняя заявка; null - не подавалась */
            request: components["schemas"]["AvatarRequestView"] | null;
        };
        /**
         * MyBadges
         * @description Свои бейджи, полученные и нет.
         */
        MyBadges: {
            /** Items */
            items: components["schemas"]["BadgeView"][];
        };
        /**
         * MyInventory
         * @description Инвентарь игрока (просроченные предметы скрыты).
         */
        MyInventory: {
            /** Items */
            items: components["schemas"]["InventoryEntry"][];
        };
        /**
         * MyProgress
         * @description Свой прогресс: уровень, опыт, серии, бусты.
         */
        MyProgress: {
            /** @description Действующий буст; null — нет */
            boost: components["schemas"]["BoostView"] | null;
            /**
             * Boosts Queued
             * @description Бустов в очереди после действующего
             */
            boosts_queued: number;
            /** Config Version */
            config_version: number;
            /** Level */
            level: number;
            /**
             * Max Level
             * @description Последний уровень конфига
             */
            max_level: number;
            /**
             * Solo Xp Cap
             * @description Потолок опыта с соло в сутки (конфиг)
             */
            solo_xp_cap: number;
            /**
             * Solo Xp Today
             * @description Опыт с соло за сегодня
             */
            solo_xp_today: number;
            /** Streaks */
            streaks: components["schemas"]["DailyStreakView"][];
            /** Total Xp */
            total_xp: number;
            /**
             * Xp In Level
             * @description Набрано внутри текущего уровня
             */
            xp_in_level: number;
            /**
             * Xp To Next
             * @description До следующего уровня; null — высший
             */
            xp_to_next: number | null;
        };
        /**
         * MyQuests
         * @description Свои задания: дневные, недельные, разовые.
         */
        MyQuests: {
            daily: components["schemas"]["QuestPeriodView"];
            one_time: components["schemas"]["QuestPeriodView"];
            weekly: components["schemas"]["QuestPeriodView"];
        };
        /**
         * MyWallet
         * @description Свой кошелёк: Версы по бакетам и последние проводки.
         */
        MyWallet: {
            /**
             * Currency
             * @default verses
             * @constant
             */
            currency: "verses";
            /**
             * Earned
             * @description Заработаны игрой
             */
            earned: number;
            /**
             * Paid
             * @description Куплены (биллинг выключен — 0)
             */
            paid: number;
            /**
             * Recent
             * @description Последние проводки, новые первыми
             */
            recent: components["schemas"]["LedgerEntryView"][];
            /** Total */
            total: number;
        };
        /**
         * PackCatalog
         * @description Витрина паков.
         */
        PackCatalog: {
            /** Packs */
            packs: components["schemas"]["PackView"][];
        };
        /**
         * PackView
         * @description Пак косметики (скрыт при `billing.enabled=false`).
         */
        PackView: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Items */
            items: components["schemas"]["ItemView"][];
            /** Key */
            key: string;
            /**
             * Price Kopecks
             * @description Цена в копейках
             */
            price_kopecks: number;
            /** Style */
            style: string;
            /** Title */
            title: string;
            /** Verses Amount */
            verses_amount: number;
        };
        /**
         * PlayerAppearance
         * @description Оформление игрока (публичное): рамка, титул, фон, аватар.
         */
        PlayerAppearance: {
            appearance: components["schemas"]["Appearance"];
            /**
             * Custom Avatar Url
             * @description Одобренная своя аватарка; null - аватар из слота
             */
            custom_avatar_url: string | null;
            /**
             * User Id
             * Format: uuid
             */
            user_id: string;
        };
        /**
         * PlayersAppearance
         * @description Оформление нескольких игроков (матч, профиль); неизвестные id пропускаются.
         */
        PlayersAppearance: {
            /** Players */
            players: components["schemas"]["PlayerAppearance"][];
        };
        /**
         * PrizeView
         * @description Приз сектора колеса.
         */
        PrizeView: {
            /**
             * Amount
             * @description Опыт или Версы; у boost, item и none — 0
             */
            amount: number;
            /**
             * Item Key
             * @description Только item
             */
            item_key: string | null;
            /**
             * Kind
             * @enum {string}
             */
            kind: "xp" | "verses" | "boost" | "item" | "none";
            /**
             * Matches
             * @description Только boost
             */
            matches: number | null;
            /**
             * Multiplier
             * @description Только boost
             */
            multiplier: number | null;
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
         * PromoBatch
         * @description Партия кодов в списке (без самих кодов).
         */
        PromoBatch: {
            /** Activations */
            activations: number;
            /** Codes Total */
            codes_total: number;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Expires At */
            expires_at: string | null;
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /** Label */
            label: string;
            /** Max Uses */
            max_uses: number;
            /** Per User Once */
            per_user_once: boolean;
            reward: components["schemas"]["PromoReward"];
            /**
             * Status
             * @enum {string}
             */
            status: "active" | "expired" | "revoked";
        };
        /**
         * PromoBatchPage
         * @description Страница партий (курсор).
         */
        PromoBatchPage: {
            /** Items */
            items: components["schemas"]["PromoBatch"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /**
         * PromoRedeemRequest
         * @description Погасить промокод.
         */
        PromoRedeemRequest: {
            /**
             * Code
             * @description Код как ввёл игрок (регистр и пробелы нормализуются)
             */
            code: string;
        };
        /**
         * PromoRedeemResult
         * @description Награда промокода. Повтор погашения того же кода возвращает ту же награду.
         */
        PromoRedeemResult: {
            /**
             * Amount
             * @description Версы; для предмета null
             */
            amount: number | null;
            item: components["schemas"]["ItemView"] | null;
            /** Repeated */
            repeated: boolean;
            /**
             * Reward Kind
             * @enum {string}
             */
            reward_kind: "verses" | "item";
        };
        /**
         * PromoReward
         * @description Награда: ровно одно из `verses_amount` и `item_id` (в соответствии с `kind`).
         */
        PromoReward: {
            /** Item Id */
            item_id?: string | null;
            /**
             * Kind
             * @enum {string}
             */
            kind: "verses" | "item";
            /** Verses Amount */
            verses_amount?: number | null;
        };
        /**
         * PublicProgress
         * @description Публичный прогресс другого игрока (профиль). Скрытие — поле модели, ручка в 5c.
         */
        PublicProgress: {
            /** Badges */
            badges: components["schemas"]["BadgeRef"][];
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
         * PublishRequest
         * @description Опубликовать черновик: новая версия действует со следующего периода заданий.
         */
        PublishRequest: {
            /**
             * Body Sha256
             * @description `body_sha256` черновика, который видел админ (иначе 409 `draft_changed`)
             */
            body_sha256: string;
            /** Reason */
            reason: string;
            /**
             * Version
             * @description Версия черновика
             */
            version: number;
        };
        /**
         * PurchaseResult
         * @description Результат покупки. Повтор с тем же `Idempotency-Key` возвращает ту же выдачу.
         */
        PurchaseResult: {
            /** Balance After */
            balance_after: number;
            item: components["schemas"]["ItemView"];
            /**
             * Repeated
             * @description true - повтор уже выполненной покупки
             */
            repeated: boolean;
        };
        /**
         * QuestPeriodView
         * @description Задания одного периода.
         */
        QuestPeriodView: {
            /** Items */
            items: components["schemas"]["QuestView"][];
            /**
             * Period
             * @enum {string}
             */
            period: "daily" | "weekly" | "one_time";
            /**
             * Period End
             * @description null у разовых
             */
            period_end: string | null;
            /**
             * Period Start
             * Format: date
             */
            period_start: string;
        };
        /**
         * QuestView
         * @description Задание игрока в текущем периоде.
         */
        QuestView: {
            /** Completed */
            completed: boolean;
            /** Description */
            description: string;
            /** Key */
            key: string;
            /** Progress */
            progress: number;
            reward: components["schemas"]["Reward"];
            /** Target */
            target: number;
            /** Title */
            title: string;
        };
        /**
         * ReverseRequest
         * @description Сторно проводки.
         */
        ReverseRequest: {
            /** Reason */
            reason: string;
        };
        /**
         * RevokeRequest
         * @description Отозвать партию: новые активации невозможны, выданные награды не отзываются.
         */
        RevokeRequest: {
            /** Reason */
            reason: string;
        };
        /**
         * Reward
         * @description Награда: опыт и Версы.
         */
        Reward: {
            /** Verses */
            verses: number;
            /** Xp */
            xp: number;
        };
        /**
         * RollbackRequest
         * @description Откат: копия старой версии становится новым черновиком.
         */
        RollbackRequest: {
            /** Reason */
            reason: string;
        };
        /**
         * SaveDraftRequest
         * @description Сохранить черновик конфига (заменяет текущий черновик).
         */
        SaveDraftRequest: {
            /** Config */
            config: {
                [key: string]: components["schemas"]["JsonValue"];
            };
            /** Reason */
            reason: string;
        };
        /**
         * SavePackRequest
         * @description Создать или заменить пак.
         */
        SavePackRequest: {
            /**
             * Active
             * @default false
             */
            active: boolean;
            /** Item Ids */
            item_ids: string[];
            /** Key */
            key: string;
            /**
             * Price Kopecks
             * @description Цена в копейках
             */
            price_kopecks: number;
            /** Reason */
            reason: string;
            /** Style */
            style: string;
            /** Title */
            title: string;
            /**
             * Verses Amount
             * @default 0
             */
            verses_amount: number;
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
         * ShopCatalog
         * @description Витрина: активные предметы, можно отфильтровать по слоту.
         */
        ShopCatalog: {
            /**
             * Balance
             * @description Версы игрока (бакет earned)
             */
            balance: number;
            /** Items */
            items: components["schemas"]["ShopItem"][];
        };
        /**
         * ShopItem
         * @description Предмет витрины с состоянием для игрока.
         */
        ShopItem: {
            item: components["schemas"]["ItemView"];
            /**
             * State
             * @description locked - не хватает уровня и цены; available - можно получить; owned - есть
             * @enum {string}
             */
            state: "locked" | "available" | "owned";
        };
        /**
         * SpinResult
         * @description Результат спина за сутки.
         */
        SpinResult: {
            /**
             * Day
             * Format: date
             */
            day: string;
            prize: components["schemas"]["PrizeView"];
            /**
             * Repeated
             * @description true — спин за эти сутки уже был, возвращён тот же
             */
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
         * UpdateItemRequest
         * @description Заменить поля предмета (`key` менять нельзя - 422 `progression.item_key_immutable`).
         */
        UpdateItemRequest: {
            /**
             * Active
             * @description Активный предмет виден на витрине; нужна загруженная картинка
             * @default false
             */
            active: boolean;
            /** Key */
            key: string;
            /** Level Required */
            level_required?: number | null;
            /**
             * Media Kind
             * @enum {string}
             */
            media_kind: "static" | "animated";
            /** Price */
            price?: number | null;
            /** Rarity */
            rarity: string;
            /** Reason */
            reason: string;
            /** Slot */
            slot: string;
            /** Title */
            title: string;
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
         * WheelSectorView
         * @description Сектор колеса; шанс публикуется игроку.
         */
        WheelSectorView: {
            /**
             * Chance
             * @description weight / сумма весов
             */
            chance: number;
            /** Key */
            key: string;
            prize: components["schemas"]["PrizeView"];
            /** Title */
            title: string;
            /** Weight */
            weight: number;
        };
        /**
         * WheelView
         * @description Колесо: секторы с шансами и состояние на сегодня.
         */
        WheelView: {
            /** Config Version */
            config_version: number;
            /**
             * Day
             * Format: date
             * @description Текущие сутки в `progression.timezone`
             */
            day: string;
            /** Enabled */
            enabled: boolean;
            /**
             * Next Spin At
             * @description Когда обновится; null — можно крутить
             */
            next_spin_at: string | null;
            /** Sectors */
            sectors: components["schemas"]["WheelSectorView"][];
            /** @description Спин за сегодня; null — ещё не крутил */
            today: components["schemas"]["SpinResult"] | null;
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
    badges_admin_v1_badges_get: {
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
                    "application/json": components["schemas"]["AdminBadgeList"];
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
    versions_admin_v1_configs_get: {
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
                    "application/json": components["schemas"]["ConfigVersionList"];
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
    version_admin_v1_configs__version__get: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Версия конфига */
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
                    "application/json": components["schemas"]["ConfigVersionBody"];
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
    rollback_admin_v1_configs__version__rollback_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description Версия конфига */
                version: number;
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
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ConfigVersionBody"];
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
    save_draft_admin_v1_configs_draft_put: {
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
                    "application/json": components["schemas"]["ConfigVersionBody"];
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
    publish_admin_v1_configs_draft_publish_post: {
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
                    "application/json": components["schemas"]["ConfigVersionBody"];
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
    items_admin_v1_items_get: {
        parameters: {
            query?: {
                active?: boolean | null;
                cursor?: string | null;
                limit?: number;
                slot?: string | null;
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
                    "application/json": components["schemas"]["AdminItemPage"];
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
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_item_admin_v1_items_post: {
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
                "application/json": components["schemas"]["CreateItemRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AdminItem"];
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
    update_item_admin_v1_items__item_id__put: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description Идентификатор */
                item_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UpdateItemRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AdminItem"];
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
    upload_item_media_admin_v1_items__item_id__media_put: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description Идентификатор */
                item_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "multipart/form-data": components["schemas"]["Body_upload_item_media_admin_v1_items__item_id__media_put"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AdminItem"];
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
            /** @description Problem Details */
            413: {
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
    reverse_admin_v1_ledger__op_id__reverse_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description `op_id` проводки */
                op_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ReverseRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LedgerOperation"];
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
    packs_admin_v1_packs_get: {
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
                    "application/json": components["schemas"]["AdminPackList"];
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
    create_pack_admin_v1_packs_post: {
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
                "application/json": components["schemas"]["SavePackRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AdminPack"];
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
    update_pack_admin_v1_packs__pack_id__put: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description Идентификатор */
                pack_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SavePackRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AdminPack"];
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
    promo_batches_admin_v1_promo_codes_get: {
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
                    "application/json": components["schemas"]["PromoBatchPage"];
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
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_promo_admin_v1_promo_codes_post: {
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
                "application/json": components["schemas"]["CreatePromoRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CreatedPromo"];
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
    revoke_promo_admin_v1_promo_codes__promo_id__revoke_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description Идентификатор */
                promo_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["RevokeRequest"];
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
        };
    };
    quests_admin_v1_quests_get: {
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
                    "application/json": components["schemas"]["AdminQuestList"];
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
    ledger_admin_v1_users__user_id__ledger_get: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
            };
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
                    "application/json": components["schemas"]["AdminLedgerPage"];
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
    grant_admin_v1_users__user_id__ledger_grant_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description Идентификатор пользователя */
                user_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["GrantRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LedgerOperation"];
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
    players_appearance_v1_appearance_get: {
        parameters: {
            query: {
                user_ids: string[];
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
                    "application/json": components["schemas"]["PlayersAppearance"];
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
    my_appearance_v1_me_appearance_get: {
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
                    "application/json": components["schemas"]["Appearance"];
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
    equip_v1_me_appearance__slot__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Ключ слота */
                slot: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EquipRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Appearance"];
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
    my_avatar_v1_me_avatar_get: {
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
                    "application/json": components["schemas"]["MyAvatar"];
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
    submit_avatar_v1_me_avatar_post: {
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
                "multipart/form-data": components["schemas"]["Body_submit_avatar_v1_me_avatar_post"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AvatarRequestView"];
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
            /** @description Problem Details */
            413: {
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
    my_badges_v1_me_badges_get: {
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
                    "application/json": components["schemas"]["MyBadges"];
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
    my_inventory_v1_me_inventory_get: {
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
                    "application/json": components["schemas"]["MyInventory"];
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
    my_progress_v1_me_progress_get: {
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
                    "application/json": components["schemas"]["MyProgress"];
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
    my_quests_v1_me_quests_get: {
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
                    "application/json": components["schemas"]["MyQuests"];
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
    my_wallet_v1_me_wallet_get: {
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
                    "application/json": components["schemas"]["MyWallet"];
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
    redeem_v1_promo_redeem_post: {
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
                "application/json": components["schemas"]["PromoRedeemRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["PromoRedeemResult"];
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
    shop_items_v1_shop_items_get: {
        parameters: {
            query?: {
                /** @description Ключ слота */
                slot?: string | null;
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
                    "application/json": components["schemas"]["ShopCatalog"];
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
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    purchase_v1_shop_items__item_id__purchase_post: {
        parameters: {
            query?: never;
            header?: {
                /** @description Обязателен. Ключ идемпотентности: 8–128 символов [A-Za-z0-9_.:-]. Повтор с тем же ключом и телом возвращает сохранённый ответ, с другим телом — 409 idempotency.key_reused. */
                "Idempotency-Key"?: string | null;
            };
            path: {
                /** @description Идентификатор предмета */
                item_id: string;
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
                    "application/json": components["schemas"]["PurchaseResult"];
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
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    shop_packs_v1_shop_packs_get: {
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
                    "application/json": components["schemas"]["PackCatalog"];
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
        };
    };
    user_progress_v1_users__user_id__progress_get: {
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
                    "application/json": components["schemas"]["PublicProgress"];
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
    wheel_v1_wheel_get: {
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
                    "application/json": components["schemas"]["WheelView"];
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
    spin_v1_wheel_spin_post: {
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
                    "application/json": components["schemas"]["SpinResult"];
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
}
