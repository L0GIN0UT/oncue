/**
 * Заблокированный аккаунт при уже открытой сессии (ADR 0052, 0069).
 *
 * Бан `account` закрывает вход, но access-токен живёт до истечения (≤15 мин), а дальше refresh
 * отвечает 403 `auth.account_banned`. Любой запрос или refresh с этим кодом переводит клиента
 * на экран «Аккаунт заблокирован» (без причины).
 */
export const ACCOUNT_BANNED = "auth.account_banned";
export const BANNED_PATH = "/banned";

type BannedHandler = () => void;

const goToBanned: BannedHandler = () => {
  // Полная навигация: из `fetch` нет роутера, а состояние сессии должно сброситься.
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- намеренно
  if (globalThis.location.pathname !== BANNED_PATH) globalThis.location.assign(BANNED_PATH);
};

let handler: BannedHandler = goToBanned;

/** Подменить действие при бане (тесты); без аргумента — вернуть переход на экран. */
export function setBannedHandler(next?: BannedHandler): void {
  handler = next ?? goToBanned;
}

/** Сессия заблокирована: перейти на экран блокировки (повторные вызовы безвредны). */
export function notifyAccountBanned(): void {
  handler();
}

/** Код Problem Details из тела ответа (клон — тело остаётся читаемым для вызывающего). */
export async function problemCodeOf(res: Response): Promise<string | null> {
  try {
    const body: unknown = await res.clone().json();
    return body && typeof body === "object" && "code" in body ? String(body.code) : null;
  } catch {
    return null;
  }
}

/** Ответ 403 `auth.account_banned`? Если да — сообщить и вернуть `true`. */
export async function reportIfBanned(res: Response): Promise<boolean> {
  if (res.status !== 403 || (await problemCodeOf(res)) !== ACCOUNT_BANNED) return false;
  notifyAccountBanned();
  return true;
}
