/**
 * Тесты правила «нет литералов цвета» (ADR 0049) на настоящем ESLint Linter: что ловится, что
 * намеренно пропускается (якоря `#add`, имена цветов, color-mix, токены).
 */
import { Linter } from "eslint";
import { describe, expect, it } from "vitest";
// @ts-expect-error — .mjs без типов; тот же набор селекторов, что в eslint.config.mjs
import { colorRestrictedSyntax } from "../../eslint-color-rule.mjs";

const linter = new Linter({ configType: "flat" });

function lint(code: string): string[] {
  return linter
    .verify(
      code,
      [
        {
          files: ["**/*.tsx"],
          languageOptions: {
            ecmaVersion: 2024,
            sourceType: "module",
            parserOptions: { ecmaFeatures: { jsx: true } },
          },
          rules: { "no-restricted-syntax": ["error", ...colorRestrictedSyntax] },
        },
      ],
      "file.tsx",
    )
    .map((m) => `${m.line}: ${m.message.slice(0, 22)}`);
}

describe("правило: литералы цвета запрещены", () => {
  it.each([
    'const a = "#16112a";',
    'const a = "#fff";',
    'const a = "#FFFF";',
    'const a = "#16112aff";',
    'const a = "bg-[#fff] text-white";',
    "const a = `color: #abc;`;",
    'const a = "rgb(1 2 3)";',
    "const a = `rgba(${r}, 0, 0, 1)`;",
    'const a = "hsl(10 20% 30%)";',
    'const a = "oklch(0.5 0.1 200)";',
    'const a = "HSLA(0,0%,0%,1)";',
    'const a = <div style={{ color: "#bad" }} />;',
    'const a = <div data-x="#add" />;',
  ])("ловит %s", (code) => {
    expect(lint(code)).not.toHaveLength(0);
  });
});

describe("правило: что пропускается", () => {
  it.each([
    ['<a href="#add">x</a>', "якорь в href"],
    ['<a href="#bad">x</a>', "якорь в href"],
    ['<a href="#face">x</a>', "якорь в href"],
    ['<a href="#main">x</a>', "якорь-слово"],
    ['document.querySelector("#add")', "селектор по id"],
    ['el.closest("#bad")', "селектор по id"],
    ['el.matches("#face")', "селектор по id"],
    ["const HEX = /^#[0-9a-fA-F]{6}$/;", "регулярка с классом символов"],
    ['const a = "var(--color-ink)";', "токен"],
    ['const a = "bg-[var(--accent,var(--color-music))]";', "токен в Tailwind"],
    ['const a = "text-ink bg-night ring-rule";', "классы токенов"],
  ])("пропускает %s (%s)", (code) => {
    expect(lint(code)).toEqual([]);
  });

  // Сознательные пропуски: правило не пытается разбирать эти значения.
  it.each([
    ['const a = "red";', "именованный цвет"],
    ['const a = "transparent";', "transparent"],
    ['const a = "currentColor";', "currentColor"],
    ['const a = "color-mix(in oklab, var(--color-ink) 50%, transparent)";', "color-mix из токенов"],
  ])("не ловит %s (%s) — сознательно", (code) => {
    expect(lint(code)).toEqual([]);
  });
});
