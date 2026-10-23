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
         * @description Бейджи действующего конфига с числом выданных. Правка — через черновик.
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
         * @description Копия старой версии становится новым черновиком. Ошибки: 404 `progression.config_version_not_found`, 409 `progression.config_never_published`.
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
         * @description Заменяет черновик; конфиг проверяется целиком. Ошибки: 422 `progression.config_invalid` (`details.errors` — пути и причины).
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
         * @description Новая версия действует для новых проводок; задания меняются со следующего периода. Ошибки: 404 `progression.config_version_not_found`, 409 `progression.config_not_draft`.
         */
        post: operations["publish_admin_v1_configs_draft_publish_post"];
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
         * @description Встречная проводка со ссылкой `reversal_of`; журнал не правится. Ошибки: 404 `progression.ledger_entry_not_found`, 409 `progression.already_reversed`, 409 `progression.insufficient_balance` (баланс ушёл бы ниже нуля).
         */
        post: operations["reverse_admin_v1_ledger__op_id__reverse_post"];
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
         * @description Задания действующего конфига с числом выполнений. Правка — через черновик.
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
         * @description Проводки Версов, новые первыми, курсорная пагинация.
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
         * @description Проводка `admin:{Idempotency-Key}`; предел — `admin.grant_max_amount` и `admin.grant_max_per_user_day` конфига. Ошибки: 404 `progression.user_not_found`, 422 `progression.grant_limit_exceeded`.
         */
        post: operations["grant_admin_v1_users__user_id__ledger_grant_post"];
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
    "/v1/me/progress": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Мой прогресс
         * @description Уровень, опыт, потолок соло, серии дейлика по доменам, буст.
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
         * @description Дневные, недельные и разовые задания с прогрессом; назначение — снимком на период.
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
         * @description Тело пустое. Один спин в сутки (`progression.timezone`): сектор выбирает сервер по весам, приз выдаётся в той же транзакции. Повтор за те же сутки возвращает тот же результат (`repeated = true`). Ошибки: 409 `progression.wheel_disabled`.
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
            /** Unlocked Total */
            unlocked_total: number;
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
         * AdminQuestCard
         * @description Задание действующего конфига со статистикой.
         */
        AdminQuestCard: {
            /** Completed Total */
            completed_total: number;
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
             * @default earned
             * @enum {string}
             */
            bucket: "earned" | "paid";
            /** Reason */
            reason: string;
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
            /** Reason */
            reason: string;
            /**
             * Version
             * @description Версия черновика (защита от гонки правок)
             */
            version: number;
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
}
