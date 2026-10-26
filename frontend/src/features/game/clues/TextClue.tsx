"use client";
/**
 * Текстовая загадка (`description`, `emoji`): JSON-ассет уровня уже скачан в `Blob`
 * (`text_asset.v1`). Блоки рисуются по реестру видов (`plot`, `tagline`, `fields`, `emoji`) —
 * без знания, какой режим или загадка их прислали. Неизвестный вид блока пропускается.
 */
import type { ComponentType } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import type { ClueStageProps } from "./types";
import {
  emojiGlyphs,
  readTextAsset,
  type TextBlock,
  type TextBlockKind,
  type TextField,
} from "./text-asset";

type BlockOf<K extends TextBlockKind> = Extract<TextBlock, { kind: K }>;
type BlockProps<K extends TextBlockKind> = { block: BlockOf<K> };

function PlotBlock({ block }: BlockProps<"plot">) {
  return <p className="text-lg leading-relaxed text-pretty">{block.text}</p>;
}

function TaglineBlock({ block }: BlockProps<"tagline">) {
  return (
    <blockquote className="border-l-4 border-music pl-4 font-display text-2xl leading-snug font-semibold text-balance">
      «{block.text}»
    </blockquote>
  );
}

function FieldsBlock({ block }: BlockProps<"fields">) {
  const t = useTranslations();
  const label = (field: TextField) => t(`game.textField.${field}`);
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2">
      {block.items.map((item) => (
        <div key={`${item.field}-${item.value}`} className="contents">
          <dt className="text-ink-faint">{label(item.field)}</dt>
          <dd className="font-medium">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

function EmojiBlock({ block }: BlockProps<"emoji">) {
  const t = useTranslations();
  const glyphs = emojiGlyphs(block.text);
  return (
    <p
      role="img"
      aria-label={t("game.emojiLabel", { count: glyphs.length, emoji: glyphs.join(" ") })}
      className="flex flex-wrap justify-center gap-3 text-6xl leading-none sm:text-7xl"
      data-testid="emoji-clue"
    >
      {glyphs.map((g, i) => (
        <span key={`${i}-${g}`} aria-hidden>
          {g}
        </span>
      ))}
    </p>
  );
}

/** Реестр блоков текстового ассета: новый вид блока — новая запись. */
export const textBlockRegistry: { [K in TextBlockKind]: ComponentType<BlockProps<K>> } = {
  plot: PlotBlock,
  tagline: TaglineBlock,
  fields: FieldsBlock,
  emoji: EmojiBlock,
};

function Block({ block }: { block: TextBlock }) {
  const Component = textBlockRegistry[block.kind] as ComponentType<{ block: TextBlock }>;
  return <Component block={block} />;
}

export function TextClue({ clue }: ClueStageProps) {
  const t = useTranslations();
  const asset = useQuery({
    queryKey: ["text-asset", clue.objectUrl],
    queryFn: () => readTextAsset(clue.blob),
    staleTime: Infinity,
    retry: false,
  });
  if (!asset.data) {
    return (
      <div className="grid min-h-44 place-items-center text-ink-muted" role="status">
        {asset.isError ? t("game.clueFailed") : t("game.clueLoading")}
      </div>
    );
  }
  return (
    <div
      className="flex min-h-44 flex-col justify-center gap-5 py-2"
      data-testid="text-clue"
      aria-live="polite"
    >
      {asset.data.blocks.map((block, i) => (
        <Block key={`${clue.level}-${i}-${block.kind}`} block={block} />
      ))}
    </div>
  );
}
