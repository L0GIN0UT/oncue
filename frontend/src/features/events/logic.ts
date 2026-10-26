/** Чистая логика экрана события: маршруты разделов, поиск спрятанного элемента, подписи. */
import type { EventFindCard, EventPageView } from "@/shared/api/types";

/**
 * Раздел `play_page` определения события → маршрут приложения (белый список контракта
 * `event_definition.v1.json`). Неизвестный раздел — `null`: кнопки «играть» нет.
 */
export const PLAY_ROUTES: Readonly<Record<string, string>> = {
  home: "/",
  daily: "/",
  solo: "/solo",
  online: "/online",
  collections: "/collections",
  leaderboards: "/leaderboards",
  wheel: "/profile",
  shop: "/shop",
  profile: "/profile",
};

export function playRoute(page: string | null | undefined): string | null {
  if (!page) return null;
  return Object.hasOwn(PLAY_ROUTES, page) ? PLAY_ROUTES[page]! : null;
}

/** Страницы, на которых может прятаться элемент (белый список контракта). */
export const SPOT_PAGE_KEYS = [
  "home",
  "leaderboards",
  "profile",
  "collections",
  "match_summary",
  "shop",
  "wheel",
] as const;
export type SpotPageKey = (typeof SPOT_PAGE_KEYS)[number];

/** Открытая находка-элемент для страницы; активное событие только. */
export function openSpotFor(
  page: Pick<EventPageView, "status" | "finds"> | null | undefined,
  pageKey: SpotPageKey,
): EventFindCard | null {
  if (!page || page.status !== "active") return null;
  return (
    page.finds.find(
      (f) => f.state === "open" && f.trigger.type === "ui_spot" && f.trigger.page_key === pageKey,
    ) ?? null
  );
}

/** Находки по номеру. */
export function sortedFinds(finds: readonly EventFindCard[]): EventFindCard[] {
  return [...finds].sort((a, b) => a.number - b.number);
}

/** Доля найденного, 0..100. */
export function progressPct(found: number, total: number): number {
  return total > 0 ? Math.min(100, Math.round((found / total) * 100)) : 0;
}

/** Точки тропы в процентах карты; найденная часть — по первым `found` узлам, остальное — пунктир. */
export function trailPaths(finds: readonly EventFindCard[]): { found: string; rest: string } {
  const pts = finds.map((f) => `${f.x_pct} ${f.y_pct}`);
  const foundCount = finds.filter((f) => f.state === "found").length;
  const line = (list: string[]) => (list.length > 1 ? `M ${list.join(" L ")}` : "");
  return {
    found: line(pts.slice(0, foundCount)),
    rest: line(pts.slice(Math.max(foundCount - 1, 0))),
  };
}
