import type { components } from "./schema/notifications";

type N = components["schemas"];

export type NotificationItem = N["NotificationItem"];
export type NotificationPage = N["NotificationPage"];
export type NotificationSettings = N["NotificationSettings"];
export type TypeSetting = N["TypeSetting"];
export type EmailSubscription = N["EmailSubscription"];
