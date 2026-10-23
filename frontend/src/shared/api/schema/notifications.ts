/* eslint-disable */
// Сгенерировано `pnpm gen:api` из contracts/openapi/notifications.json. Не править руками.

export interface paths {
    "/admin/v1/broadcasts": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Рассылки */
        get: operations["broadcasts_admin_v1_broadcasts_get"];
        put?: never;
        /**
         * Поставить рассылку
         * @description Информационное письмо подписчикам (согласие и отписка). Отправку делает задача RabbitMQ. Повтор с тем же `Idempotency-Key` возвращает ту же рассылку.
         */
        post: operations["create_broadcast_admin_v1_broadcasts_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/devices": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Зарегистрировать устройство для Push
         * @description Push пока не отправляется (порт без реализации); повтор токена - то же устройство.
         */
        post: operations["register_device_v1_devices_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/devices/{device_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /**
         * Удалить устройство
         * @description Идемпотентно; чужое устройство - 404.
         */
        delete: operations["delete_device_v1_devices__device_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/email-subscription": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Согласие на рассылку */
        get: operations["email_subscription_v1_email_subscription_get"];
        /**
         * Дать или отозвать согласие на рассылку
         * @description По умолчанию согласия нет; рассылка без согласия не уходит.
         */
        put: operations["set_email_subscription_v1_email_subscription_put"];
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
    "/v1/notification-settings": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Настройки уведомлений
         * @description Типы из конфига с текущими каналами игрока.
         */
        get: operations["settings_v1_notification_settings_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/notification-settings/{type_key}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /**
         * Изменить настройку типа
         * @description Идемпотентно по природе операции. Неизвестный тип - 404 `notifications.type_not_found`; тип с `user_mutable = false` - 422 `notifications.type_not_mutable`.
         */
        put: operations["set_setting_v1_notification_settings__type_key__put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/notifications": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Мои уведомления
         * @description Новые первыми, курсорная пагинация; `unread_only` - только непрочитанные.
         */
        get: operations["notifications_v1_notifications_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/notifications/{notification_id}/read": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Отметить прочитанным
         * @description Идемпотентно. Чужое или несуществующее - 404 `notifications.not_found`.
         */
        post: operations["read_one_v1_notifications__notification_id__read_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/notifications/read-all": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Прочитать все
         * @description Идемпотентно: повтор отмечает 0.
         */
        post: operations["read_all_v1_notifications_read_all_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/notifications/unread-count": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Число непрочитанных */
        get: operations["unread_count_v1_notifications_unread_count_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/unsubscribe": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Отписаться по ссылке из письма
         * @description Единственная ручка без входа. Токен - HMAC серверным ключом над `user_id` и рассылкой, сравнение за постоянное время; чужой, просроченный и неверный токен дают один и тот же ответ (существование пользователя не раскрывается). Лимит попыток по IP (Redis, fail-closed): 429 с `Retry-After`. Повтор - 204. Неверный токен - 422 `notifications.unsubscribe_invalid`.
         */
        post: operations["unsubscribe_v1_unsubscribe_post"];
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
         * Broadcast
         * @description Рассылка.
         */
        Broadcast: {
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
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Recipients
             * @description Подписчиков на момент постановки
             */
            recipients: number;
            /**
             * Status
             * @enum {string}
             */
            status: "queued" | "sending" | "sent" | "failed";
            /** Subject */
            subject: string;
        };
        /**
         * BroadcastPage
         * @description Страница рассылок (курсор).
         */
        BroadcastPage: {
            /** Items */
            items: components["schemas"]["Broadcast"][];
            /** Next Cursor */
            next_cursor: string | null;
        };
        /**
         * BroadcastRequest
         * @description Информационная рассылка подписчикам.
         */
        BroadcastRequest: {
            /** Body */
            body: string;
            /** Reason */
            reason: string;
            /**
             * Subject
             * @description Тема письма: без управляющих символов (защита заголовков письма)
             */
            subject: string;
        };
        /**
         * Device
         * @description Зарегистрированное устройство.
         */
        Device: {
            /**
             * Id
             * Format: uuid
             */
            id: string;
            /**
             * Platform
             * @enum {string}
             */
            platform: "web" | "ios" | "android";
            /**
             * Registered At
             * Format: date-time
             */
            registered_at: string;
        };
        /**
         * DeviceRequest
         * @description Регистрация устройства для Push (реализация Push - после релиза).
         */
        DeviceRequest: {
            /**
             * Platform
             * @enum {string}
             */
            platform: "web" | "ios" | "android";
            /** Token */
            token: string;
        };
        /**
         * EmailSubscription
         * @description Согласие на информационную рассылку от администрации.
         */
        EmailSubscription: {
            /** Changed At */
            changed_at: string | null;
            /** Subscribed */
            subscribed: boolean;
        };
        /**
         * EmailSubscriptionRequest
         * @description Дать или отозвать согласие на рассылку.
         */
        EmailSubscriptionRequest: {
            /** Subscribed */
            subscribed: boolean;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /**
         * MarkedRead
         * @description Сколько уведомлений отмечено прочитанными.
         */
        MarkedRead: {
            /** Marked */
            marked: number;
        };
        /**
         * NotificationItem
         * @description Уведомление игрока: текст уже собран по шаблону и локали.
         */
        NotificationItem: {
            /** Body */
            body: string;
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
            /**
             * Read At
             * @description null - не прочитано
             */
            read_at: string | null;
            /** Title */
            title: string;
            /** Type */
            type: string;
        };
        /**
         * NotificationPage
         * @description Страница уведомлений (курсор), новые первыми.
         */
        NotificationPage: {
            /** Items */
            items: components["schemas"]["NotificationItem"][];
            /** Next Cursor */
            next_cursor: string | null;
            /** Unread */
            unread: number;
        };
        /**
         * NotificationSettings
         * @description Настройки по типам.
         */
        NotificationSettings: {
            /** Types */
            types: components["schemas"]["TypeSetting"][];
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
         * TypeSetting
         * @description Настройка типа уведомления.
         */
        TypeSetting: {
            /** Email */
            email: boolean;
            /** In App */
            in_app: boolean;
            /**
             * Title
             * @description Название типа для экрана настроек (конфиг, локаль игрока)
             */
            title: string;
            /** Type */
            type: string;
            /**
             * User Mutable
             * @description false - тип нельзя отключить
             */
            user_mutable: boolean;
        };
        /**
         * TypeSettingRequest
         * @description Включить или отключить каналы типа.
         */
        TypeSettingRequest: {
            /** Email */
            email: boolean;
            /** In App */
            in_app: boolean;
        };
        /**
         * UnreadCount
         * @description Число непрочитанных (бейдж колокольчика).
         */
        UnreadCount: {
            /** Count */
            count: number;
        };
        /**
         * UnsubscribeRequest
         * @description Отписка по подписанной ссылке из письма (one-click).
         */
        UnsubscribeRequest: {
            /** Token */
            token: string;
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
    broadcasts_admin_v1_broadcasts_get: {
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
                    "application/json": components["schemas"]["BroadcastPage"];
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
    create_broadcast_admin_v1_broadcasts_post: {
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
                "application/json": components["schemas"]["BroadcastRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Broadcast"];
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
    register_device_v1_devices_post: {
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
                "application/json": components["schemas"]["DeviceRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Device"];
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
    delete_device_v1_devices__device_id__delete: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Идентификатор */
                device_id: string;
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
    email_subscription_v1_email_subscription_get: {
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
                    "application/json": components["schemas"]["EmailSubscription"];
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
    set_email_subscription_v1_email_subscription_put: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["EmailSubscriptionRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EmailSubscription"];
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
                    "application/json": components["schemas"]["ServiceMeta"];
                };
            };
        };
    };
    settings_v1_notification_settings_get: {
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
                    "application/json": components["schemas"]["NotificationSettings"];
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
    set_setting_v1_notification_settings__type_key__put: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Тип */
                type_key: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TypeSettingRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TypeSetting"];
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
    notifications_v1_notifications_get: {
        parameters: {
            query?: {
                cursor?: string | null;
                limit?: number;
                unread_only?: boolean;
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
                    "application/json": components["schemas"]["NotificationPage"];
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
    read_one_v1_notifications__notification_id__read_post: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description Идентификатор */
                notification_id: string;
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
    read_all_v1_notifications_read_all_post: {
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
                    "application/json": components["schemas"]["MarkedRead"];
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
    unread_count_v1_notifications_unread_count_get: {
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
                    "application/json": components["schemas"]["UnreadCount"];
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
    unsubscribe_v1_unsubscribe_post: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UnsubscribeRequest"];
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
