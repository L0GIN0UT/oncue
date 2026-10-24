/**
 * Проверка контраста наборов тем (WCAG 2.1 AA): разбор `--color-*: #rrggbb` из CSS темы и
 * отношение яркостей. Используется тестом `contrast.test.ts` — в рантайме приложения не нужен.
 */

export type TokenMap = Record<string, string>;

const COLOR_DECL = /--color-([\w-]+)\s*:\s*(#[0-9a-fA-F]{6})\s*;/g;

/** Цветовые токены набора (`night` → `#16112a`); значения не в `#rrggbb` (rgb(), var()) пропускаются. */
export function parseColorTokens(css: string): TokenMap {
  const stripped = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const out: TokenMap = {};
  for (const m of stripped.matchAll(COLOR_DECL)) out[m[1]!] = m[2]!.toLowerCase();
  return out;
}

function channel(v: number): number {
  const s = v / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

export function luminance(hex: string): number {
  const n = Number.parseInt(hex.slice(1), 16);
  return (
    0.2126 * channel((n >> 16) & 255) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255)
  );
}

/** Отношение контраста двух цветов `#rrggbb`: от 1 до 21. */
export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number];
  return (hi + 0.05) / (lo + 0.05);
}

export const AA_TEXT = 4.5;
export const AA_LARGE_UI = 3;

/** Пара токенов «передний план / фон» и порог. */
export type ContrastPair = { fg: string; bg: string; min: number; what: string };

const SURFACES = ["night", "stage", "stage-raised"] as const;

/** Основные пары: текст на поверхностях, акценты и исходы как текст, текст на акценте. */
export const CORE_PAIRS: ContrastPair[] = [
  ...SURFACES.flatMap((bg) => [
    { fg: "ink", bg, min: AA_TEXT, what: "основной текст" },
    { fg: "ink-muted", bg, min: AA_TEXT, what: "вторичный текст" },
    { fg: "ink-faint", bg, min: AA_TEXT, what: "приглушённый текст" },
    { fg: "music", bg, min: AA_TEXT, what: "акцент музыки" },
    { fg: "film", bg, min: AA_TEXT, what: "акцент кино" },
    { fg: "exact", bg, min: AA_TEXT, what: "исход: точно" },
    { fg: "partial", bg, min: AA_TEXT, what: "исход: частично" },
    { fg: "miss", bg, min: AA_TEXT, what: "исход: мимо" },
  ]),
  { fg: "night", bg: "ink", min: AA_TEXT, what: "кнопка: тёмный текст на светлой заливке" },
  { fg: "night", bg: "music", min: AA_TEXT, what: "текст на акценте музыки" },
  { fg: "night", bg: "film", min: AA_TEXT, what: "текст на акценте кино" },
  { fg: "ink", bg: "music-deep", min: AA_LARGE_UI, what: "крупный текст на тёмном акценте музыки" },
  { fg: "ink", bg: "film-deep", min: AA_LARGE_UI, what: "крупный текст на тёмном акценте кино" },
  { fg: "skip", bg: "night", min: AA_LARGE_UI, what: "UI: пропуск" },
  { fg: "ink", bg: "night", min: AA_LARGE_UI, what: "UI: кольцо фокуса" },
];

/** Нарушения порога для набора (пары, чьих токенов в наборе нет, пропускаются). */
export function contrastViolations(tokens: TokenMap, pairs: ContrastPair[] = CORE_PAIRS): string[] {
  const out: string[] = [];
  for (const p of pairs) {
    const fg = tokens[p.fg];
    const bg = tokens[p.bg];
    if (!fg || !bg) continue;
    const ratio = contrastRatio(fg, bg);
    if (ratio < p.min) out.push(`${p.fg} на ${p.bg} (${p.what}): ${ratio.toFixed(2)} < ${p.min}`);
  }
  return out;
}

/** Имена токенов, участвующих в проверяемых парах. */
export const CORE_TOKEN_NAMES = [...new Set(CORE_PAIRS.flatMap((p) => [p.fg, p.bg]))];

const COLOR_DECL_ANY = /--color-([\w-]+)\s*:\s*([^;]+);/g;

/**
 * Токены из `CORE_PAIRS`, заданные в теме не как `#rrggbb` (oklch(), rgb(), var()…): контраст
 * таких значений парсер не считает, поэтому тест темы обязан падать, а не молча пропускать пару.
 */
export function nonHexCoreTokens(css: string): string[] {
  const stripped = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const out: string[] = [];
  for (const m of stripped.matchAll(COLOR_DECL_ANY)) {
    const name = m[1]!;
    if (CORE_TOKEN_NAMES.includes(name) && !/^#[0-9a-fA-F]{6}$/.test(m[2]!.trim())) out.push(name);
  }
  return out;
}
