/**
 * Иконки по ключу темы (ADR 0049): «ключ темы → набор». В 5b есть только `default`; у сезонной
 * темы (5d) будет свой набор с тем же списком имён. Неизвестный ключ — набор `default`.
 */
import { resolveThemeKey, type ThemeKey } from "@/styles/theme";
import { defaultIcons, type IconName, type IconSet } from "./default";

export type { IconName, IconSet };

export const ICON_SETS: Record<ThemeKey, IconSet> = {
  default: defaultIcons,
};

export function iconsFor(themeKey: unknown): IconSet {
  return ICON_SETS[resolveThemeKey(themeKey)];
}
