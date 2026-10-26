"use client";
/**
 * Состав черновика: порядок (перетаскивание мышью и с клавиатуры: ручка + стрелки, кнопки
 * «выше/ниже»), отметка играбельности в выбранном режиме, свои эмодзи с проверкой на клиенте
 * (графемы — `Intl.Segmenter`), удаление. Названия — из catalog (`GET /v1/items/{id}`).
 */
import {
  useState,
  type ComponentProps,
  type DragEvent,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslations } from "next-intl";
import { ArrowDown, ArrowUp, GripVertical, Trash2 } from "lucide-react";
import { api, unwrap } from "@/shared/api/client";
import type { DraftItem, MetaLimits } from "@/shared/api/types";
import { cn } from "@/shared/lib/cn";
import { checkEmoji } from "../emoji";
import { errorText, type FieldError } from "../errors";

export type ItemLite = { title: string; year: number | null; subtitle: string | null };

export const itemLiteKey = (id: string) => ["catalog", "item-lite", id] as const;

function useItemLite(id: string) {
  return useQuery({
    queryKey: itemLiteKey(id),
    queryFn: async (): Promise<ItemLite> => {
      const item = unwrap(
        await api().catalog.GET("/v1/items/{item_id}", { params: { path: { item_id: id } } }),
      );
      return { title: item.title, year: item.year, subtitle: item.subtitle };
    },
    staleTime: 10 * 60_000,
  });
}

type Props = {
  items: DraftItem[];
  limits: MetaLimits | null;
  /** id играбельных в выбранном режиме; `null` — неизвестно (режим не выбран, запрос идёт). */
  playable: Set<string> | null;
  errors: FieldError[];
  onMove: (from: number, to: number) => void;
  onRemove: (index: number) => void;
  onEmoji: (index: number, emoji: string | null) => void;
};

export function ItemList({ items, limits, playable, errors, onMove, onRemove, onEmoji }: Props) {
  const t = useTranslations();
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [announce, setAnnounce] = useState("");

  const move = (from: number, to: number) => {
    if (to < 0 || to >= items.length) return;
    onMove(from, to);
    setAnnounce(t("collections.editor.moved", { position: to + 1, total: items.length }));
    // Фокус остаётся на ручке перемещённой строки.
    requestAnimationFrame(() =>
      document.querySelector<HTMLElement>(`[data-handle-index="${to}"]`)?.focus(),
    );
  };

  if (!items.length) return <p className="text-ink-muted">{t("collections.editor.empty")}</p>;

  return (
    <>
      <ol className="flex flex-col gap-2.5" data-testid="editor-items">
        {items.map((item, index) => (
          <ItemRow
            key={item.media_item_id}
            item={item}
            index={index}
            total={items.length}
            limits={limits}
            playable={playable}
            errors={errors.filter(
              (e) => e.path === `items[${index}]` || e.path.startsWith(`items[${index}].`),
            )}
            dragging={dragFrom === index}
            onDragStart={() => setDragFrom(index)}
            onDragEnd={() => setDragFrom(null)}
            onDrop={() => {
              if (dragFrom !== null) move(dragFrom, index);
              setDragFrom(null);
            }}
            onMove={move}
            onRemove={() => onRemove(index)}
            onEmoji={(emoji) => onEmoji(index, emoji)}
          />
        ))}
      </ol>
      <p className="sr-only" aria-live="polite">
        {announce}
      </p>
    </>
  );
}

type RowProps = {
  item: DraftItem;
  index: number;
  total: number;
  limits: MetaLimits | null;
  playable: Set<string> | null;
  errors: FieldError[];
  dragging: boolean;
  onDragStart: () => void;
  onDragEnd: () => void;
  onDrop: () => void;
  onMove: (from: number, to: number) => void;
  onRemove: () => void;
  onEmoji: (emoji: string | null) => void;
};

function ItemRow(p: RowProps) {
  const t = useTranslations();
  const lite = useItemLite(p.item.media_item_id);
  const title = lite.data?.title ?? t("collections.editor.itemLoading");
  const emoji = p.item.custom?.emoji ?? "";
  const check =
    emoji && p.limits
      ? checkEmoji(emoji, p.limits.emoji_min_graphemes, p.limits.emoji_max_graphemes)
      : "ok";
  const ok = p.playable?.has(p.item.media_item_id);

  const onHandleKey = (e: KeyboardEvent<HTMLButtonElement>) => {
    if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      e.preventDefault();
      p.onMove(p.index, p.index + (e.key === "ArrowUp" ? -1 : 1));
    }
  };

  return (
    <li
      draggable
      onDragStart={(e: DragEvent) => {
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", String(p.index));
        p.onDragStart();
      }}
      onDragEnd={p.onDragEnd}
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        p.onDrop();
      }}
      data-testid={`editor-item-${p.item.media_item_id}`}
      className={cn(
        "grid grid-cols-[auto_auto_1fr_auto] items-start gap-3 rounded-[var(--radius-control)] border-[1.5px] border-rule bg-stage px-3 py-3",
        p.dragging && "opacity-50",
        p.errors.length > 0 && "border-miss-line",
      )}
    >
      <button
        type="button"
        data-handle-index={p.index}
        onKeyDown={onHandleKey}
        className="grid size-11 cursor-grab place-items-center text-ink-muted hover:text-ink"
        aria-label={t("collections.editor.handle", { title, position: p.index + 1 })}
        aria-describedby="editor-handle-help"
      >
        <GripVertical aria-hidden className="size-5" />
      </button>
      <span className="tabular mt-3 w-6 text-right text-ink-muted">{p.index + 1}</span>
      <div className="flex min-w-0 flex-col gap-2">
        <p className="min-w-0">
          <span dir="auto" className="block truncate font-semibold">
            {title}
          </span>
          <span className="text-sm text-ink-muted">
            {lite.data?.year ?? ""}
            {p.playable && (
              <span
                className={cn("ml-2", ok ? "text-exact" : "text-miss")}
                data-testid={`editor-playable-${p.item.media_item_id}`}
                data-playable={ok ? "true" : "false"}
              >
                {ok
                  ? t("collections.editor.playable")
                  : emoji
                    ? t("collections.editor.playableCustom")
                    : t("collections.editor.notPlayable")}
              </span>
            )}
          </span>
        </p>
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-ink-muted">{t("collections.editor.emoji")}</span>
          <input
            value={emoji}
            onChange={(e) => p.onEmoji(e.target.value.trim() || null)}
            aria-invalid={check !== "ok" || p.errors.length > 0 ? true : undefined}
            inputMode="text"
            className="h-11 rounded-[10px] border-[1.5px] border-rule bg-night px-3 text-lg outline-none focus:border-music focus:shadow-[0_0_0_4px_var(--color-glow)] aria-[invalid=true]:border-miss"
            data-testid={`editor-emoji-${p.item.media_item_id}`}
          />
          {check !== "ok" && (
            <span className="text-miss" data-testid={`editor-emoji-error-${p.item.media_item_id}`}>
              {t(`collections.editor.emojiCheck.${check}`, {
                min: p.limits?.emoji_min_graphemes ?? 0,
                max: p.limits?.emoji_max_graphemes ?? 0,
              })}
            </span>
          )}
        </label>
        {p.errors.map((e) => (
          <p key={`${e.path}:${e.code}`} className="text-sm text-miss" role="alert">
            {errorText(t, e)}
          </p>
        ))}
      </div>
      <div className="flex flex-col gap-1">
        <IconButton
          label={t("collections.editor.up")}
          disabled={p.index === 0}
          onClick={() => p.onMove(p.index, p.index - 1)}
        >
          <ArrowUp aria-hidden className="size-4" />
        </IconButton>
        <IconButton
          label={t("collections.editor.down")}
          disabled={p.index === p.total - 1}
          onClick={() => p.onMove(p.index, p.index + 1)}
        >
          <ArrowDown aria-hidden className="size-4" />
        </IconButton>
        <IconButton label={t("collections.editor.remove")} onClick={p.onRemove}>
          <Trash2 aria-hidden className="size-4" />
        </IconButton>
      </div>
    </li>
  );
}

function IconButton({
  label,
  children,
  ...props
}: { label: string; children: ReactNode } & ComponentProps<"button">) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      className="grid size-11 place-items-center rounded-full text-ink-muted hover:bg-stage-raised hover:text-ink disabled:opacity-30"
      {...props}
    >
      {children}
    </button>
  );
}
