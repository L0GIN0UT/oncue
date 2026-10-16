/**
 * Проверка своих эмодзи на клиенте (D5): только эмодзи, от `min` до `max` графем
 * (`limits.emoji_min_graphemes` / `emoji_max_graphemes` из `GET /v1/meta`). Графемы считает
 * `Intl.Segmenter`, поэтому флаг или семья из нескольких кодовых точек — одна графема.
 * Окончательная проверка (строго RGI) — на сервере при публикации.
 */

export type EmojiCheck = "ok" | "empty" | "not_emoji" | "too_short" | "too_long";

const PICTOGRAPHIC = /\p{Extended_Pictographic}/u;
const REGIONAL = /^\p{Regional_Indicator}{2}$/u;
const KEYCAP = /^[0-9#*]️?⃣$/u;
/** Что допустимо внутри графемы эмодзи, кроме самих пиктограмм. */
const JOINERS = /[‍️⃣\u{1F3FB}-\u{1F3FF}\u{E0020}-\u{E007F}]/gu;

/** Графемы строки (`Intl.Segmenter`; без него — кодовые точки). */
export function graphemes(text: string): string[] {
  if (typeof Intl !== "undefined" && "Segmenter" in Intl) {
    const segmenter = new Intl.Segmenter(undefined, { granularity: "grapheme" });
    return [...segmenter.segment(text)].map((s) => s.segment);
  }
  return [...text];
}

function isEmojiGrapheme(g: string): boolean {
  if (REGIONAL.test(g) || KEYCAP.test(g)) return true;
  if (!PICTOGRAPHIC.test(g)) return false;
  // Всё, кроме пиктограмм и связок (ZWJ, VS16, тона кожи, теги флагов), — текст.
  return [...g.replace(JOINERS, "")].every((c) => PICTOGRAPHIC.test(c));
}

export function checkEmoji(raw: string, min: number, max: number): EmojiCheck {
  const text = raw.trim();
  if (!text) return "empty";
  const parts = graphemes(text);
  if (!parts.every(isEmojiGrapheme)) return "not_emoji";
  if (parts.length < min) return "too_short";
  if (parts.length > max) return "too_long";
  return "ok";
}
