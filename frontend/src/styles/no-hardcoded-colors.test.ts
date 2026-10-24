/**
 * ADR 0049: в исходниках нет литералов цвета (#hex, rgb(), rgba(), hsl(), hsla(), oklch()) —
 * только токены из `src/styles`. Тест — вторая линия после правила eslint `no-restricted-syntax`
 * (eslint.config.mjs): он же ловит комментарии, шаблонные строки и классы Tailwind вида `bg-[#fff]`.
 * Исключения: `src/styles/**`, тесты, сгенерированные типы API.
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const srcDir = path.resolve(import.meta.dirname, "..");

// Должно совпадать с COLOR_LITERAL в eslint.config.mjs.
const HEX = /#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})(?![0-9A-Za-z_-])/;
const FUNC = /\b(?:rgba?|hsla?|oklch|oklab|hwb)\(/i;

export function colorLiteralsIn(source: string): string[] {
  const found: string[] = [];
  source.split(/\r?\n/).forEach((line, i) => {
    // Комментарии тоже проверяем: цвет в комментарии — всё равно повод вынести в токен.
    // Якоря (`href="#add"`) и DOM-селекторы по id (`querySelector("#bad")`) — не цвета.
    const code = line
      .replace(/href=["']#[^"']*["']/g, "")
      .replace(/(?:querySelector(?:All)?|closest|matches)\(["'`]#[^"'`]*["'`]\)/g, "");
    if (HEX.test(code) || FUNC.test(code)) found.push(`${i + 1}: ${line.trim()}`);
  });
  return found;
}

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = path.join(dir, name);
    const rel = path.relative(srcDir, full).split(path.sep).join("/");
    if (statSync(full).isDirectory()) {
      return rel === "styles" || rel === "shared/api/schema" || rel === "test" ? [] : sources(full);
    }
    return /\.(ts|tsx)$/.test(name) && !/\.test\.(ts|tsx)$/.test(name) ? [full] : [];
  });
}

describe("colorLiteralsIn", () => {
  it("находит #hex, rgb(), hsl() и классы с произвольным цветом", () => {
    expect(colorLiteralsIn('const a = "#16112a";')).toHaveLength(1);
    expect(colorLiteralsIn('className="bg-[#fff] text-red"')).toHaveLength(1);
    expect(colorLiteralsIn("const c = `rgb(1 2 3)`;")).toHaveLength(1);
    expect(colorLiteralsIn("x = hsla(0, 0%, 0%, 1)")).toHaveLength(1);
  });

  it("не ругается на якоря, id и токены", () => {
    expect(colorLiteralsIn('<a href="#main">')).toEqual([]);
    expect(colorLiteralsIn('<a href="#add">x</a>')).toEqual([]);
    expect(colorLiteralsIn('el.querySelector("#face")')).toEqual([]);
    expect(colorLiteralsIn('const a = "red"; // имена цветов — сознательный пропуск')).toEqual([]);
    expect(colorLiteralsIn('style={{ color: "var(--color-ink)" }}')).toEqual([]);
    expect(colorLiteralsIn("const HEX = /^#[0-9a-fA-F]{6}$/;")).toEqual([]);
  });
});

describe("исходники без захардкоженных цветов", () => {
  it("в src/**/*.{ts,tsx} (кроме src/styles и тестов) нет литералов цвета", () => {
    const violations = sources(srcDir).flatMap((file) =>
      colorLiteralsIn(readFileSync(file, "utf8")).map(
        (v) => `${path.relative(srcDir, file).split(path.sep).join("/")}:${v}`,
      ),
    );
    expect(violations).toEqual([]);
  });
});
