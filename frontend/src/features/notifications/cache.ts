/** Чистые обновления кэша списка уведомлений (бесконечный запрос по курсору). */
import type { InfiniteData } from "@tanstack/react-query";
import type { NotificationItem, NotificationPage } from "@/shared/api/types";

export type ListData = InfiniteData<NotificationPage, string | null>;

const mapItems = (data: ListData, fn: (item: NotificationItem) => NotificationItem): ListData => ({
  ...data,
  pages: data.pages.map((p) => ({ ...p, items: p.items.map(fn) })),
});

/** Отметить одно уведомление прочитанным (повтор безопасен). */
export function withRead(data: ListData, id: string, at: string): ListData {
  return mapItems(data, (i) => (i.id === id && i.read_at === null ? { ...i, read_at: at } : i));
}

export function withAllRead(data: ListData, at: string): ListData {
  return mapItems(data, (i) => (i.read_at === null ? { ...i, read_at: at } : i));
}

/** Новое уведомление — в начало первой страницы (если его там ещё нет). */
export function withPrepended(data: ListData, item: NotificationItem): ListData {
  if (data.pages.some((p) => p.items.some((i) => i.id === item.id))) return data;
  const [first, ...rest] = data.pages;
  if (!first) return data;
  return { ...data, pages: [{ ...first, items: [item, ...first.items] }, ...rest] };
}

export const unreadIn = (data: Pick<ListData, "pages">): number =>
  data.pages.flatMap((p) => p.items).filter((i) => i.read_at === null).length;
