"use client";
/**
 * Ключ активной темы для клиентских компонентов (иконки по теме). Ключ известен при серверном
 * рендере (layout читает `/v1/pages/shell`), поэтому мигания нет; сами токены применяет CSS
 * по `data-theme` на <html>.
 */
import { createContext, useContext, type ReactNode } from "react";
import { DEFAULT_THEME_KEY, type ThemeKey } from "@/styles/theme";
import { iconsFor, type IconSet } from "./icons";

const ThemeContext = createContext<ThemeKey>(DEFAULT_THEME_KEY);

export function ThemeProvider({ themeKey, children }: { themeKey: ThemeKey; children: ReactNode }) {
  return <ThemeContext.Provider value={themeKey}>{children}</ThemeContext.Provider>;
}

export function useThemeKey(): ThemeKey {
  return useContext(ThemeContext);
}

/** Набор иконок активной темы. */
export function useIcons(): IconSet {
  return iconsFor(useThemeKey());
}
