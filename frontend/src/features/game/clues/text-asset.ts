/**
 * JSON-ассет текстовых загадок (`description`, `emoji`) по контракту
 * `contracts/clues/text_asset.v1.json` (ADR 0024): `{version: 1, blocks: [{kind, text|items}]}`.
 *
 * Ассет пишет media-pipeline, клиент только показывает блоки. Разбор строгий к форме и
 * терпимый к новому: блок неизвестного вида пропускается (его нет в реестре блоков), а
 * испорченный файл — ошибка загрузки уровня.
 */

export const TEXT_BLOCK_KINDS = ["plot", "tagline", "fields", "emoji"] as const;
export type TextBlockKind = (typeof TEXT_BLOCK_KINDS)[number];

export const TEXT_FIELDS = ["year", "genres", "country", "director", "cast_top3"] as const;
export type TextField = (typeof TEXT_FIELDS)[number];

export type TextFieldItem = { field: TextField; value: string };

type TextKind = Exclude<TextBlockKind, "fields">;

export type TextBlock =
  | { [K in TextKind]: { kind: K; text: string } }[TextKind]
  | { kind: "fields"; items: TextFieldItem[] };

export type TextAsset = { version: 1; blocks: TextBlock[] };

export class TextAssetError extends Error {
  constructor(reason: string) {
    super(`text asset invalid: ${reason}`);
    this.name = "TextAssetError";
  }
}

const isRecord = (v: unknown): v is Record<string, unknown> =>
  !!v && typeof v === "object" && !Array.isArray(v);

const isKind = (v: unknown): v is TextBlockKind =>
  typeof v === "string" && (TEXT_BLOCK_KINDS as readonly string[]).includes(v);

const isField = (v: unknown): v is TextField =>
  typeof v === "string" && (TEXT_FIELDS as readonly string[]).includes(v);

function parseBlock(raw: unknown): TextBlock | null {
  if (!isRecord(raw) || !isKind(raw.kind)) return null;
  if (raw.kind === "fields") {
    const items = Array.isArray(raw.items)
      ? raw.items.flatMap((i): TextFieldItem[] =>
          isRecord(i) && isField(i.field) && typeof i.value === "string" && i.value.trim()
            ? [{ field: i.field, value: i.value }]
            : [],
        )
      : [];
    return items.length ? { kind: "fields", items } : null;
  }
  return typeof raw.text === "string" && raw.text.trim()
    ? { kind: raw.kind, text: raw.text }
    : null;
}

/** Разобрать JSON ассета; без единого показываемого блока — {@link TextAssetError}. */
export function parseTextAsset(raw: unknown): TextAsset {
  if (!isRecord(raw) || raw.version !== 1 || !Array.isArray(raw.blocks))
    throw new TextAssetError("shape");
  const blocks = raw.blocks.map(parseBlock).filter((b): b is TextBlock => b !== null);
  if (!blocks.length) throw new TextAssetError("no blocks");
  return { version: 1, blocks };
}

/** Прочитать ассет из `Blob` (файл уже скачан по подписанной ссылке). */
export async function readTextAsset(blob: Blob): Promise<TextAsset> {
  let raw: unknown;
  try {
    raw = JSON.parse(await blob.text());
  } catch {
    throw new TextAssetError("json");
  }
  return parseTextAsset(raw);
}

/** Эмодзи строки по графемам (составные эмодзи с ZWJ и модификаторами — один символ). */
export function emojiGlyphs(text: string): string[] {
  const segmenter = new Intl.Segmenter(undefined, { granularity: "grapheme" });
  return [...segmenter.segment(text)].map((s) => s.segment).filter((s) => s.trim());
}
