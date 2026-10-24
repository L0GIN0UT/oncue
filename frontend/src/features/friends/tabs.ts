/** Вкладки экрана друзей. Вынесено из клиентского модуля: страница-сервер читает `?tab=`. */
export type TabId = "friends" | "requests" | "add" | "blocks" | "privacy";

export const TAB_IDS: readonly TabId[] = ["friends", "requests", "add", "blocks", "privacy"];

/** Вкладка из `?tab=`; неизвестное значение — список друзей. */
export function tabFromParam(value: string | null | undefined): TabId {
  return TAB_IDS.find((id) => id === value) ?? "friends";
}
