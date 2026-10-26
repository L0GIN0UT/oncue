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

  it("сезонная тема winter известна (5d)", () => {
    expect(resolveThemeKey("winter")).toBe("winter");
    expect(themeKeyOfShell({ theme: { key: "winter" } })).toBe("winter");
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

describe("состав токенов «Ночного эфира»", () => {
  const css = readFileSync(path.join(stylesDir, "themes", "default.css"), "utf8");
  const names = [...css.matchAll(/--color-([\w-]+)\s*:/g)].map((m) => m[1]);

  it("default задаёт все токены дизайн-системы", () => {
    for (const token of [
      "night",
      "stage",
      "stage-raised",
      "rule",
      "rule-strong",
      "ink",
      "ink-soft",
      "ink-muted",
      "ink-faint",
      "ink-dim",
      "music",
      "exact",
      "exact-fill",
      "exact-line",
      "partial",
      "partial-fill",
      "partial-line",
      "miss",
      "miss-fill",
      "miss-line",
      "skip",
      "skip-fill",
      "skip-line",
      "glow",
    ])
      expect(names, token).toContain(token);
  });

  it("акцент один: музыка и кино равны янтарю", () => {
    const value = (n: string) => new RegExp(`--color-${n}:\s*([^;]+);`).exec(css)?.[1];
    expect(value("film")).toBe(value("music"));
    expect(value("partial")).toBe(value("music"));
  });

  it("winter переопределяет только известные токены default", () => {
    const winter = readFileSync(path.join(stylesDir, "themes", "winter.css"), "utf8");
    for (const m of winter.matchAll(/--color-([\w-]+)\s*:/g)) expect(names, m[1]).toContain(m[1]);
  });
});
