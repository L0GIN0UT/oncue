/**
 * Уведомления о друзьях (`friend_request`, `friend_accepted`): куда ведёт строка. Если сервер
 * положил в `data.user_id` игрока (отправитель заявки или принявший), ссылка ведёт в его профиль,
 * где работают «Принять/Отклонить» и «Добавить». Пока поля нет — на вкладку заявок (`friend_request`)
 * или в список друзей (`friend_accepted`). Идентификатор проверяется по формату UUID: произвольная
 * строка в адрес не попадает.
 */
import type { NotificationItem } from "@/shared/api/types";

export const FRIEND_REQUEST_TYPE = "friend_request";
export const FRIEND_ACCEPTED_TYPE = "friend_accepted";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export type FriendNotificationLink = {
  href: string;
  /** Ключ i18n подписи действия (`notifications.*`). */
  labelKey:
    "notifications.openProfile" | "notifications.openRequests" | "notifications.openFriends";
};

export function friendNotificationLink(
  item: Pick<NotificationItem, "type" | "data">,
): FriendNotificationLink | null {
  if (item.type !== FRIEND_REQUEST_TYPE && item.type !== FRIEND_ACCEPTED_TYPE) return null;
  const raw = item.data?.user_id;
  if (typeof raw === "string" && UUID.test(raw)) {
    return { href: `/players/${raw.toLowerCase()}`, labelKey: "notifications.openProfile" };
  }
  return item.type === FRIEND_REQUEST_TYPE
    ? { href: "/friends?tab=requests", labelKey: "notifications.openRequests" }
    : { href: "/friends", labelKey: "notifications.openFriends" };
}
