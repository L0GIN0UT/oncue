/**
 * Реестр тем (ADR 0049). Бэкенд отдаёт только ключ активной темы (`/v1/pages/shell` → `theme.key`),
 * значения токенов лежат во фронтенде: `themes/<key>.css`. Неизвестный ключ — откат на `default`.
 * Новая тема: файл набора, `@import` в `tokens.css`, ключ здесь (согласованность проверяет тест).
 */

/** Ключи тем, наборы которых есть во фронтенде. */
export const THEME_KEYS = ["default"] as const;

export type ThemeKey = (typeof THEME_KEYS)[number];

export const DEFAULT_THEME_KEY: ThemeKey = "default";

/**
 * Параметры темы вне CSS: цвет адресной строки мобильных браузеров (`<meta name="theme-color">`)
 * не умеет читать CSS-переменные. Значение должно совпадать с `--color-night` набора.
 */
export const THEME_META: Record<ThemeKey, { themeColor: string }> = {
  default: { themeColor: "#16112a" },
};

const KNOWN = new Set<string>(THEME_KEYS);

/** Ключ из данных → известный ключ темы или `default`. */
export function resolveThemeKey(key: unknown): ThemeKey {
  return typeof key === "string" && KNOWN.has(key) ? (key as ThemeKey) : DEFAULT_THEME_KEY;
}

/** Ключ темы из ответа shell; `null` (сбой bff) и неполный ответ дают `default`. */
export function themeKeyOfShell(shell: { theme?: { key?: unknown } } | null | undefined): ThemeKey {
  return resolveThemeKey(shell?.theme?.key);
}
