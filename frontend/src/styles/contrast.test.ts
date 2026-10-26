/** Контраст WCAG AA для основных пар токенов каждой темы из `themes/*.css` (ADR 0049). */
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { THEME_KEYS, THEME_META } from "./theme";
import { describe, expect, it } from "vitest";
import {
  CORE_PAIRS,
  contrastRatio,
  contrastViolations,
  nonHexCoreTokens,
  parseColorTokens,
  type TokenMap,
} from "./contrast";

const themesDir = path.join(import.meta.dirname, "themes");

describe("contrastRatio", () => {
  it("известные значения: чёрное на белом 21, одинаковые 1", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrastRatio("#777777", "#777777")).toBeCloseTo(1, 5);
  });

  it("симметрична", () => {
    expect(contrastRatio("#141414", "#f2f2f2")).toBeCloseTo(
      contrastRatio("#f2f2f2", "#141414"),
      10,
    );
  });
});

describe("parseColorTokens", () => {
  it("читает #rrggbb, пропускает rgb() и комментарии", () => {
    const css = `/* --color-fake: #ffffff; */ @theme { --color-a: #AABBCC; --color-b: rgb(1 2 3 / .5); --color-c-d: #000000; }`;
    expect(parseColorTokens(css)).toEqual({ a: "#aabbcc", "c-d": "#000000" });
  });
});

describe("контраст тем из src/styles/themes", () => {
  const themes = readdirSync(themesDir).filter((f) => f.endsWith(".css"));

  it("есть хотя бы тема default", () => {
    expect(themes).toContain("default.css");
  });

  const base = parseColorTokens(readFileSync(path.join(themesDir, "default.css"), "utf8"));

  it.each(themes)("%s: основные пары проходят WCAG AA", (file) => {
    const own = parseColorTokens(readFileSync(path.join(themesDir, file), "utf8"));
    // Не-default тема переопределяет часть токенов поверх default.
    const tokens: TokenMap = file === "default.css" ? own : { ...base, ...own };
    // Токен вне #rrggbb контраст не посчитать — это ошибка темы, а не пропуск пары.
    expect(nonHexCoreTokens(readFileSync(path.join(themesDir, file), "utf8"))).toEqual([]);
    expect(contrastViolations(tokens)).toEqual([]);
    // Тема действительно определяет цвета, а не пуста.
    expect(Object.keys(own).length).toBeGreaterThan(0);
  });

  it("набор пар охватывает все ключевые токены default", () => {
    const used = new Set(CORE_PAIRS.flatMap((p) => [p.fg, p.bg]));
    for (const key of ["night", "stage", "stage-raised", "ink", "ink-muted", "ink-faint"])
      expect(used.has(key)).toBe(true);
    for (const key of used) expect(base[key], key).toBeDefined();
  });
});

describe("themeColor темы совпадает с --color-night", () => {
  const base = parseColorTokens(readFileSync(path.join(themesDir, "default.css"), "utf8"));
  it.each(THEME_KEYS)("%s", (key) => {
    const own = parseColorTokens(readFileSync(path.join(themesDir, `${key}.css`), "utf8"));
    expect(THEME_META[key].themeColor.toLowerCase()).toBe({ ...base, ...own }.night);
  });
});

describe("проверка ловит плохую тему (фикстура, не прод)", () => {
  const bad: TokenMap = {
    night: "#141414",
    stage: "#1c1c1c",
    "stage-raised": "#262626",
    ink: "#3a3a3a",
  };

  it("токен в oklch() помечается как ошибка темы, а не пропускается", () => {
    const css = `[data-theme="x"] { --color-ink: oklch(0.95 0.02 300); --color-night: #141414; --color-glow-music: rgb(1 2 3 / .1); }`;
    expect(nonHexCoreTokens(css)).toEqual(["ink"]);
    expect(nonHexCoreTokens("@theme { --color-ink: #f2f2f2; }")).toEqual([]);
  });

  it("низкий контраст текста даёт нарушения", () => {
    expect(contrastViolations(bad).length).toBeGreaterThan(0);
  });

  it("хорошая вторая тема-фикстура проходит поверх default", () => {
    const base = parseColorTokens(readFileSync(path.join(themesDir, "default.css"), "utf8"));
    const fixture = parseColorTokens(`
      [data-theme="fixture"] { --color-night: #0b1d12; --color-stage: #123020; --color-stage-raised: #1b4530; --color-ink: #f4fff4; --color-ink-muted: #c9e6c9; --color-ink-faint: #b4d6b4; }
    `);
    expect(contrastViolations({ ...base, ...fixture })).toEqual([]);
  });
});
