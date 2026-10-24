/** Ключ темы запроса на сервере: один вызов shell на рендер (layout и viewport делят результат). */
import "server-only";
import { cache } from "react";
import { themeKeyOfShell, type ThemeKey } from "@/styles/theme";
import { fetchShellPage } from "./server";

/** Сбой shell или неизвестный ключ не ломают страницу — тема `default` (ADR 0049). */
export const getThemeKey = cache(async (): Promise<ThemeKey> =>
  themeKeyOfShell(await fetchShellPage()),
);
