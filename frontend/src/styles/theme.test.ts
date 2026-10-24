/** Темы как данные (ADR 0049): ключ из shell, откат на default, файлы наборов ↔ реестр ключей. */
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { DEFAULT_THEME_KEY, THEME_KEYS, resolveThemeKey, themeKeyOfShell } from "./theme";

const stylesDir = import.meta.dirname;

describe("resolveThemeKey", () => {
  it("известный ключ возвращает как есть", () => {
    expect(resolveThemeKey("default")).toBe("default");
  });

  it.each([undefined, null, "", "halloween", "DEFAULT", "__proto__", "constructor", 42])(
    "неизвестное или пустое значение (%s) — default",
    (value) => {
      expect(resolveThemeKey(value)).toBe(DEFAULT_THEME_KEY);
    },
  );
});

describe("themeKeyOfShell", () => {
  it("берёт theme.key из ответа shell", () => {
    expect(themeKeyOfShell({ theme: { key: "default" } })).toBe("default");
  });

  it("сбой shell (null) и мусор не ломают страницу — default", () => {
    expect(themeKeyOfShell(null)).toBe(DEFAULT_THEME_KEY);
    expect(themeKeyOfShell({ theme: { key: "unknown" } })).toBe(DEFAULT_THEME_KEY);
    expect(themeKeyOfShell({} as never)).toBe(DEFAULT_THEME_KEY);
  });
});

describe("наборы тем на диске", () => {
  const files = readdirSync(path.join(stylesDir, "themes"))
    .filter((f) => f.endsWith(".css"))
    .map((f) => f.replace(/\.css$/, ""))
    .sort();

  it("каждый ключ реестра имеет файл, каждый файл — ключ", () => {
    expect([...THEME_KEYS].sort()).toEqual(files);
  });

  it("tokens.css подключает ровно эти наборы", () => {
    const css = readFileSync(path.join(stylesDir, "tokens.css"), "utf8");
    const imported = [...css.matchAll(/@import "\.\/themes\/([\w-]+)\.css"/g)].map((m) => m[1]);
    expect(imported.sort()).toEqual(files);
  });

  it("default есть всегда", () => {
    expect(THEME_KEYS).toContain(DEFAULT_THEME_KEY);
  });
});
