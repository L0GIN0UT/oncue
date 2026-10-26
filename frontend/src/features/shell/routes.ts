/** Какая оболочка и какая ширина у маршрута: список правится по мере перевода экранов на v3. */

/** Игровые маршруты: вместо шапки сайта — оболочка игры `GameShell`. */
const GAME_ROUTE = /^\/(play|match|room)(\/|$)/;

export function isGameRoute(pathname: string): boolean {
  return GAME_ROUTE.test(pathname);
}

/**
 * Страницы на полной ширине 1280 px (макеты v3). Остальные пока в колонке 640 px — их переводит
 * каждый агент вместе с экраном: добавил экран на v3 — добавь его маршрут сюда.
 */
const WIDE_ROUTES = [
  "/",
  "/solo",
  "/online",
  "/lobbies",
  "/collections",
  "/profile",
  "/players",
  "/leaderboards",
  "/shop",
];

export function isWideRoute(pathname: string): boolean {
  return WIDE_ROUTES.some((r) => (r === "/" ? pathname === "/" : pathname.startsWith(r)));
}

export type NavKey = "home" | "solo" | "online" | "collections";

export const NAV_ITEMS: readonly { key: NavKey; href: string }[] = [
  { key: "home", href: "/" },
  { key: "solo", href: "/solo" },
  { key: "online", href: "/online" },
  { key: "collections", href: "/collections" },
];

/** Активный пункт навигации по адресу; на остальных страницах — ни один. */
export function activeNav(pathname: string): NavKey | null {
  if (pathname === "/") return "home";
  if (pathname.startsWith("/solo")) return "solo";
  if (pathname.startsWith("/online") || pathname.startsWith("/lobbies")) return "online";
  if (pathname.startsWith("/collections")) return "collections";
  return null;
}
